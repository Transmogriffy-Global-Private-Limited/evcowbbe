"""Admin-only, transaction-backed reusable commercial configuration.

This module does not create quotes, settle money, command HAL or issue invoices.
All mutations require current transaction-level RBAC and audit evidence.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from uuid import UUID

from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone

from cow.common.errors import reject
from cow.domain.commercial_machine import (
    CommercialError, GSTProfile as GSTConfig, validate_tariff,
    Assignment, GSTAssignment, Context, Measurement, evaluate,
)
from cow.models import (
    AuditEvent, CustomerProfile, PricingPolicy, ServiceZone,
    CommercialPricingGroup, CommercialGroupMembership,
    CommercialTariffPayload, CommercialGSTProfile,
    CommercialTariffAssignment, CommercialGSTAssignment, CommercialZoneChannel,
)
from cow.services.zone_admin import _actor, _lock, _reason
from cow.services import rbac


def _err(e: CommercialError):
    reject(422, e.code, str(e))


def _audit(actor, obj, action, reason, **details):
    AuditEvent.objects.create(actor=actor, target_type=type(obj).__name__, target_id=obj.pk,
                              action=action, details={'reason':_reason(reason),**details})


def _get(qs, id, noun):
    obj=qs.filter(pk=id).first()
    if obj is None:
        reject(404,noun+'_NOT_FOUND',noun.replace('_',' ').title()+' not found')
    return obj


def tariff_view(p):
    payload=p.commercial_payload
    return {'id':str(p.pk),'key':p.policy_key,'version':p.policy_version,
            'status':p.status,'state_version':p.state_version,
            'channel':payload.channel,'plan':deepcopy(payload.plan),
            'created_at':p.created_at.isoformat()}


def gst_view(p):
    return {'id':str(p.pk),'key':p.profile_key,'version':p.profile_version,
            'status':p.status,'seller_tax_ref':p.seller_tax_ref,
            'legal_decision_ref':p.legal_decision_ref,'tax_classes':deepcopy(p.tax_classes),
            'created_by_id':str(p.created_by_id),
            'approved_by_id':str(p.approved_by_id) if p.approved_by_id else None}


def group_view(x):
    return {'id':str(x.pk),'name':x.name,'description':x.description,'is_active':x.is_active}


def tariff_assignment_view(x):
    return {'id':str(x.pk),'policy_id':str(x.policy_id),'target_type':x.target_type,
            'target_id':str(x.zone_id if x.target_type=='ZONE' else x.group_id),
            'channel':x.channel,'slot':x.slot,
            'starts_at':x.starts_at.isoformat() if x.starts_at else None,
            'ends_at':x.ends_at.isoformat() if x.ends_at else None}


def gst_assignment_view(x):
    return {'id':str(x.pk),'zone_id':str(x.zone_id),'channel':x.channel,
            'profile_id':str(x.profile_id),
            'starts_at':x.starts_at.isoformat() if x.starts_at else None,
            'ends_at':x.ends_at.isoformat() if x.ends_at else None}


def create_tariff(actor, plan, reason):
    try:
        validate_tariff(plan)
    except CommercialError as exc:
        _err(exc)
    with transaction.atomic():
        actor=_actor(actor,'pricing.manage')
        key=plan['key']
        _lock('commercial-library:'+key)
        last=PricingPolicy.objects.filter(policy_key=key).order_by('-policy_version').first()
        version=(last.policy_version+1 if last else 1)
        if version!=plan['version']:
            reject(409,'TARIFF_VERSION_CONFLICT','Expected next immutable tariff version')
        try:
            p=PricingPolicy.objects.create(name=key,revision=version,policy_key=key,
                policy_version=version,scope={'kind':'LIBRARY_COMPOSITE','key':f'{key}:{version}'},
                rules=[],currency='INR',state_version=1,status='DRAFT',created_by=actor)
            CommercialTariffPayload.objects.create(policy=p,channel=plan['channel'],plan=deepcopy(plan),created_by=actor)
            _audit(actor,p,'COMMERCIAL_TARIFF_DRAFT',reason,channel=plan['channel'])
        except IntegrityError:
            reject(409,'TARIFF_VERSION_CONFLICT','Tariff version conflicts with existing records')
    return tariff_view(p)


def duplicate_tariff(actor, source_policy_id, new_key, reason):
    source=_get(PricingPolicy.objects.select_related('commercial_payload'),source_policy_id,'TARIFF')
    if not hasattr(source,'commercial_payload'):
        reject(404,'TARIFF_NOT_FOUND','Reusable tariff not found')
    copied=deepcopy(source.commercial_payload.plan)
    copied['key']=new_key
    copied['version']=1
    return create_tariff(actor,copied,reason)


def publish_tariff(actor, policy_id, expected_version, reason):
    with transaction.atomic():
        actor=_actor(actor,'pricing.manage')
        _lock('commercial-policy:'+str(policy_id))
        p=_get(PricingPolicy.objects.select_related('commercial_payload').select_for_update(of=('self',)),policy_id,'TARIFF')
        if not hasattr(p,'commercial_payload'):
            reject(404,'TARIFF_NOT_FOUND','Reusable tariff not found')
        if p.state_version!=expected_version:
            reject(412,'RESOURCE_VERSION_MISMATCH','Tariff revision changed, reload ETag')
        if p.status!='DRAFT':
            reject(409,'TARIFF_STATE_CONFLICT','Only a draft can be published')
        try:validate_tariff(p.commercial_payload.plan)
        except CommercialError as exc:_err(exc)
        p.status='PUBLISHED';p.state_version+=1
        p.save(update_fields=['status','state_version','updated_at'])
        _audit(actor,p,'COMMERCIAL_TARIFF_PUBLISHED',reason)
    return tariff_view(p)


def create_group(actor, name, description, reason):
    name=name.strip();description=description.strip()
    if not 1<=len(name)<=100 or len(description)>400:
        reject(422,'INVALID_GROUP','Invalid group name or description')
    with transaction.atomic():
        actor=_actor(actor,'pricing.manage')
        _lock('commercial-group:'+name.casefold())
        if CommercialPricingGroup.objects.filter(name__iexact=name).exists():
            reject(409,'GROUP_EXISTS','Pricing Group name already in use')
        obj=CommercialPricingGroup.objects.create(name=name,description=description,created_by=actor)
        _audit(actor,obj,'COMMERCIAL_GROUP_CREATED',reason)
    return group_view(obj)


def change_membership(actor, customer_id, group_id, reason):
    with transaction.atomic():
        actor=_actor(actor,'pricing.manage')
        customer=_get(CustomerProfile.objects.select_for_update(),customer_id,'CUSTOMER')
        group=_get(CommercialPricingGroup.objects,group_id,'GROUP')
        if not group.is_active:
            reject(409,'GROUP_INACTIVE','Cannot assign a retired Group')
        _lock('commercial-membership:'+str(customer.pk))
        now=timezone.now()
        active=list(CommercialGroupMembership.objects.select_for_update().filter(customer=customer,starts_at__lte=now)
                    .filter(Q(ends_at__isnull=True)|Q(ends_at__gt=now)))
        if len(active)>1:
            reject(409,'GROUP_CONFLICT','Multiple active pricing memberships')
        if active and active[0].group_id==group.pk:
            return {'customer_id':str(customer.pk),'group_id':str(group.pk),'already_assigned':True}
        if active:
            item=active[0]
            item.ends_at=now;item.save(update_fields=['ends_at'])
        item=CommercialGroupMembership.objects.create(customer=customer,group=group,starts_at=now,created_by=actor)
        _audit(actor,item,'COMMERCIAL_GROUP_MEMBERSHIP_CHANGED',reason,customer_id=str(customer.pk),group_id=str(group.pk))
    return {'customer_id':str(customer.pk),'group_id':str(group.pk),'already_assigned':False}


def create_gst_profile(actor, key, version, seller, legal_ref, classes, reason):
    try:
        from cow.domain.commercial_machine import valid_key
        valid_key(key,'GST profile key')
        GSTConfig(id=key,version=version,seller_ref=seller,legal_decision_ref=legal_ref,
                  status='DRAFT',tax_classes=classes)
    except CommercialError as exc:_err(exc)
    with transaction.atomic():
        actor=_actor(actor,'pricing.manage','invoices.manage')
        _lock('commercial-gst-profile:'+key)
        latest=CommercialGSTProfile.objects.filter(profile_key=key).order_by('-profile_version').first()
        if version!=(latest.profile_version+1 if latest else 1):
            reject(409,'GST_VERSION_CONFLICT','Expected next GST profile version')
        obj=CommercialGSTProfile.objects.create(profile_key=key,profile_version=version,
            seller_tax_ref=seller,legal_decision_ref=legal_ref,tax_classes=deepcopy(classes),created_by=actor)
        _audit(actor,obj,'COMMERCIAL_GST_DRAFT',reason)
    return gst_view(obj)


def duplicate_gst_profile(actor, source_id, new_key, reason):
    old=_get(CommercialGSTProfile.objects,source_id,'GST_PROFILE')
    return create_gst_profile(actor,new_key,1,old.seller_tax_ref,old.legal_decision_ref,
                              deepcopy(old.tax_classes),reason)


def approve_gst_profile(actor, profile_id, reason):
    with transaction.atomic():
        actor=_actor(actor,'pricing.manage','invoices.manage')
        _lock('commercial-gst-approval:'+str(profile_id))
        obj=_get(CommercialGSTProfile.objects.select_for_update(),profile_id,'GST_PROFILE')
        if obj.status!='DRAFT':
            reject(409,'GST_STATE_CONFLICT','Only draft GST versions may be approved')
        try:GSTConfig(str(obj.pk),obj.profile_version,obj.seller_tax_ref,obj.legal_decision_ref,'APPROVED',obj.tax_classes)
        except CommercialError as exc:_err(exc)
        # Recorded authorization is not automatic confirmation of legal correctness.
        obj.status='APPROVED';obj.approved_by=actor
        obj.save(update_fields=['status','approved_by'])
        _audit(actor,obj,'COMMERCIAL_GST_APPROVED',reason,legal_decision_ref=obj.legal_decision_ref,
               note='Recorded authorized decision, not independent GST verification')
    return gst_view(obj)


def _target(target_type, target_id):
    if target_type=='ZONE':
        zone=_get(ServiceZone.objects,target_id,'ZONE')
        if zone.status!='PUBLISHED':reject(409,'ZONE_NOT_PUBLISHED','Target must be a published Zone')
        return {'zone':zone,'group':None}
    if target_type=='GROUP':
        group=_get(CommercialPricingGroup.objects,target_id,'GROUP')
        if not group.is_active:reject(409,'GROUP_INACTIVE','Target pricing Group is inactive')
        return {'zone':None,'group':group}
    reject(422,'TARGET_INVALID','Target must be a Zone or Group')


def assign_tariff(actor,policy_id,target_type,target_id,channel,slot,starts_at,ends_at,reason):
    try:
        Assignment('NEW','TMP',1,target_type,str(target_id),channel,slot,starts_at,ends_at)
    except CommercialError as exc:_err(exc)
    with transaction.atomic():
        actor=_actor(actor,'pricing.manage')
        _lock(f'commercial-assignment:{target_type}:{target_id}:{channel}:{slot}')
        target=_target(target_type,target_id)
        p=_get(PricingPolicy.objects.select_related('commercial_payload'),policy_id,'TARIFF')
        if p.status!='PUBLISHED' or not hasattr(p,'commercial_payload') or p.commercial_payload.channel!=channel:
            reject(409,'TARIFF_NOT_ELIGIBLE','Published matching-channel reusable tariff required')
        now=timezone.now()
        if starts_at is not None and ends_at is not None and ends_at<=now:
            reject(409,'ASSIGNMENT_EXPIRED','Cannot create already expired assignment')
        try:
            item=CommercialTariffAssignment.objects.create(policy=p,target_type=target_type,
                channel=channel,slot=slot,starts_at=starts_at,ends_at=ends_at,
                created_by=actor,**target)
        except IntegrityError:
            reject(409,'ASSIGNMENT_CONFLICT','Target assignment overlaps existing tariff')
        _audit(actor,item,'COMMERCIAL_TARIFF_ASSIGNED',reason,policy_id=str(p.pk),target_type=target_type)
    return tariff_assignment_view(item)


def replace_tariff(actor,assignment_id,new_policy_id,reason):
    with transaction.atomic():
        actor=_actor(actor,'pricing.manage')
        _lock('commercial-tariff-replace:'+str(assignment_id))
        identity=_get(CommercialTariffAssignment.objects,assignment_id,'ASSIGNMENT')
        _lock(f'commercial-assignment:{identity.target_type}:{identity.zone_id or identity.group_id}:{identity.channel}:{identity.slot}')
        old=_get(CommercialTariffAssignment.objects.select_for_update(),assignment_id,'ASSIGNMENT')
        now=timezone.now()
        if old.ends_at is not None or (old.starts_at is not None and old.starts_at>=now):
            reject(409,'ASSIGNMENT_NOT_REPLACEABLE','Only effective open default may be replaced; scheduled revisions require review')
        p=_get(PricingPolicy.objects.select_related('commercial_payload'),new_policy_id,'TARIFF')
        if p.status!='PUBLISHED' or not hasattr(p,'commercial_payload') or p.commercial_payload.channel!=old.channel:
            reject(409,'TARIFF_NOT_ELIGIBLE','Published matching-channel tariff required')
        if p.pk==old.policy_id:
            return tariff_assignment_view(old)
        old.ends_at=now;old.save(update_fields=['ends_at'])
        item=CommercialTariffAssignment.objects.create(policy=p,target_type=old.target_type,zone=old.zone,
            group=old.group,channel=old.channel,slot=old.slot,starts_at=now,created_by=actor)
        _audit(actor,item,'COMMERCIAL_TARIFF_REPLACED',reason,replaces_id=str(old.pk))
    return tariff_assignment_view(item)


def assign_gst(actor,profile_id,zone_id,channel,starts_at,ends_at,reason):
    try:GSTAssignment('NEW',str(zone_id),channel,str(profile_id),1,starts_at,ends_at)
    except CommercialError as exc:_err(exc)
    with transaction.atomic():
        actor=_actor(actor,'pricing.manage','invoices.manage')
        _lock(f'commercial-gst-assignment:{zone_id}:{channel}')
        zone=_get(ServiceZone.objects,zone_id,'ZONE')
        profile=_get(CommercialGSTProfile.objects,profile_id,'GST_PROFILE')
        if zone.status!='PUBLISHED' or profile.status!='APPROVED':
            reject(409,'GST_NOT_ELIGIBLE','Published Zone and approved GST Profile required')
        item=CommercialGSTAssignment.objects.create(zone=zone,channel=channel,profile=profile,
            starts_at=starts_at,ends_at=ends_at,created_by=actor)
        _audit(actor,item,'COMMERCIAL_GST_ASSIGNED',reason,profile_id=str(profile.pk))
    return gst_assignment_view(item)


def replace_gst(actor,assignment_id,new_profile_id,reason):
    with transaction.atomic():
        actor=_actor(actor,'pricing.manage','invoices.manage')
        identity=_get(CommercialGSTAssignment.objects,assignment_id,'GST_ASSIGNMENT')
        _lock(f'commercial-gst-assignment:{identity.zone_id}:{identity.channel}')
        old=_get(CommercialGSTAssignment.objects.select_for_update(),assignment_id,'GST_ASSIGNMENT')
        now=timezone.now()
        if old.ends_at is not None or (old.starts_at is not None and old.starts_at>=now):
            reject(409,'ASSIGNMENT_NOT_REPLACEABLE','Only effective open GST default may be replaced')
        profile=_get(CommercialGSTProfile.objects,new_profile_id,'GST_PROFILE')
        if profile.status!='APPROVED':reject(409,'GST_NOT_APPROVED','Approved replacement required')
        old_profile=_get(CommercialGSTProfile.objects,old.profile_id,'GST_PROFILE')
        if profile.seller_tax_ref!=old_profile.seller_tax_ref:
            reject(409,'GST_SELLER_MISMATCH','GST replacement cannot silently change the supplier')
        if old.profile_id==profile.pk:return gst_assignment_view(old)
        old.ends_at=now;old.save(update_fields=['ends_at'])
        item=CommercialGSTAssignment.objects.create(zone=old.zone,channel=old.channel,
            profile=profile,starts_at=now,created_by=actor)
        _audit(actor,item,'COMMERCIAL_GST_REPLACED',reason,replaces_id=str(old.pk))
    return gst_assignment_view(item)


def set_channel(actor,zone_id,channel,enabled,reason):
    if channel not in ('ROAMING','ON_SITE_QR') or type(enabled) is not bool:
        reject(422,'INVALID_CHANNEL','Valid channel and boolean required')
    with transaction.atomic():
        actor=_actor(actor,'pricing.manage','invoices.manage')
        _lock(f'commercial-zone-channel:{zone_id}:{channel}')
        zone=_get(ServiceZone.objects,zone_id,'ZONE')
        if enabled and zone.status!='PUBLISHED':
            reject(409,'ZONE_NOT_PUBLISHED','Publish Zone first')
        if enabled:
            ready=channel_readiness(actor,zone.pk,channel)
            if not ready['configuration_ready']:
                reject(409,'ZONE_COMMERCIAL_NOT_READY',', '.join(ready['blockers']))
        item,created=CommercialZoneChannel.objects.select_for_update().get_or_create(zone=zone,channel=channel,defaults={'enabled':False})
        if item.enabled != enabled:
            item.enabled=enabled;item.save(update_fields=['enabled','updated_at'])
            _audit(actor,item,'COMMERCIAL_CHANNEL_ENABLED' if enabled else 'COMMERCIAL_CHANNEL_PAUSED',reason,
                   zone_id=str(zone.pk),channel=channel)
        # Deferred DB trigger verifies default tariff/GST coverage at commit.
    return {'zone_id':str(zone.pk),'channel':channel,'enabled':item.enabled}


def preview(actor,zone_id,channel,relation,at,seller_ref,group_id,measurements,event):
    rbac.require(actor,'pricing.read')
    if channel not in ('ROAMING','ON_SITE_QR') or relation not in ('INSIDE','OUTSIDE'):
        reject(422,'INVALID_PREVIEW','Unsupported channel or relation')
    at=at or timezone.now()
    zone=_get(ServiceZone.objects,zone_id,'ZONE')
    scope=Q(zone_id=zone.pk)
    if group_id is not None:
        scope |= Q(group_id=group_id)
    assignments=list(CommercialTariffAssignment.objects.filter(scope,channel=channel).select_related('policy'))
    payloads=CommercialTariffPayload.objects.select_related('policy').filter(
        channel=channel,policy_id__in=[x.policy_id for x in assignments])
    tariffs={(x.policy.policy_key,x.policy.policy_version):x.plan for x in payloads if x.policy.status=='PUBLISHED'}
    tariff_rows=[]
    for x in assignments:
        if x.policy.status!='PUBLISHED':continue
        tariff_rows.append(Assignment(str(x.pk),x.policy.policy_key,x.policy.policy_version,
            x.target_type,str(x.zone_id if x.target_type=='ZONE' else x.group_id),
            x.channel,x.slot,x.starts_at,x.ends_at))
    bound=list(CommercialGSTAssignment.objects.filter(zone=zone,channel=channel).select_related('profile'))
    gst_rows=[GSTAssignment(str(x.pk),str(x.zone_id),x.channel,str(x.profile_id),x.profile.profile_version,
        x.starts_at,x.ends_at) for x in bound]
    gst_profiles={(str(x.profile.pk),x.profile.profile_version):GSTConfig(str(x.profile.pk),x.profile.profile_version,
        x.profile.seller_tax_ref,x.profile.legal_decision_ref,x.profile.status,x.profile.tax_classes)
        for x in bound}
    # Admin supplied location, seller and group are SCENARIO values, NEVER trusted service evidence.
    ctx=Context(str(zone.pk),channel,relation,at,seller_ref,pricing_group_id=str(group_id) if group_id else None,
        serviceable=zone.status=='PUBLISHED',operational=zone.status=='PUBLISHED',verified=False)
    normalized={name:Measurement(value['value'],value['source'],True) for name,value in measurements.items()}
    try:
        result=evaluate(context=ctx,event=event,assignments=tariff_rows,tariffs=tariffs,
            gst_assignments=gst_rows,gst_profiles=gst_profiles,measurements=normalized,mode='PREVIEW')
    except CommercialError as exc:_err(exc)
    result.update(simulation_only=True,may_accept_quote=False,position_trusted=False)
    return result


def close_tariff_assignment(actor, assignment_id, reason):
    """Remove exactly one target binding; DB readiness blocks last active Zone root."""
    with transaction.atomic():
        actor=_actor(actor,'pricing.manage')
        identity=_get(CommercialTariffAssignment.objects,assignment_id,'ASSIGNMENT')
        _lock(f'commercial-assignment:{identity.target_type}:{identity.zone_id or identity.group_id}:{identity.channel}:{identity.slot}')
        row=_get(CommercialTariffAssignment.objects.select_for_update(),assignment_id,'ASSIGNMENT')
        now=timezone.now()
        if row.ends_at is not None or (row.starts_at is not None and row.starts_at>=now):
            reject(409,'ASSIGNMENT_NOT_CLOSABLE','Only an effective assignment can be closed')
        row.ends_at=now
        row.save(update_fields=['ends_at'])
        _audit(actor,row,'COMMERCIAL_TARIFF_TARGET_REMOVED',reason,
               policy_id=str(row.policy_id),target=str(row.zone_id or row.group_id))
        # Deferred database readiness guarantee rejects removing final active Zone default.
    return tariff_assignment_view(row)


def close_gst_assignment(actor, assignment_id, reason):
    with transaction.atomic():
        actor=_actor(actor,'pricing.manage','invoices.manage')
        identity=_get(CommercialGSTAssignment.objects,assignment_id,'GST_ASSIGNMENT')
        _lock(f'commercial-gst-assignment:{identity.zone_id}:{identity.channel}')
        row=_get(CommercialGSTAssignment.objects.select_for_update(),assignment_id,'GST_ASSIGNMENT')
        now=timezone.now()
        if row.ends_at is not None or (row.starts_at is not None and row.starts_at>=now):
            reject(409,'GST_ASSIGNMENT_NOT_CLOSABLE','Only an effective GST assignment can be closed')
        row.ends_at=now;row.save(update_fields=['ends_at'])
        _audit(actor,row,'COMMERCIAL_GST_TARGET_REMOVED',reason,profile_id=str(row.profile_id))
    return gst_assignment_view(row)


def duplicate_and_assign(actor,source_policy_id,new_key,target_type,target_id,channel,reason):
    """One Admin action: validated copy, publish and target assignment atomically.

    Targets with an existing default must use Replace, not implicit overwrite.
    """
    with transaction.atomic():
        new=duplicate_tariff(actor,source_policy_id,new_key,reason)
        published=publish_tariff(actor,UUID(new['id']),1,reason)
        assigned=assign_tariff(actor,UUID(published['id']),target_type,target_id,channel,
                               'DEFAULT',None,None,reason)
        return {'tariff':published,'assignment':assigned}


def channel_readiness(actor,zone_id,channel):
    rbac.require(actor,'pricing.read')
    if channel not in {'ROAMING','ON_SITE_QR'}:
        reject(422,'INVALID_CHANNEL','Unsupported channel')
    zone=_get(ServiceZone.objects,zone_id,'ZONE')
    now=timezone.now()
    roots=list(CommercialTariffAssignment.objects.filter(zone=zone,channel=channel,slot='DEFAULT',
        policy__status='PUBLISHED').filter(Q(starts_at__isnull=True)|Q(starts_at__lte=now))
        .filter(Q(ends_at__isnull=True)|Q(ends_at__gt=now)).select_related('policy__commercial_payload'))
    gst=list(CommercialGSTAssignment.objects.filter(zone=zone,channel=channel,
        profile__status='APPROVED').filter(Q(starts_at__isnull=True)|Q(starts_at__lte=now))
        .filter(Q(ends_at__isnull=True)|Q(ends_at__gt=now)).select_related('profile'))
    blockers=[]
    if zone.status!='PUBLISHED':blockers.append('ZONE_NOT_PUBLISHED')
    if len(roots)!=1:blockers.append('ZONE_DEFAULT_TARIFF_REQUIRED')
    if len(gst)!=1:blockers.append('GST_ASSIGNMENT_REQUIRED')
    if len(roots)==1 and len(gst)==1:
        payload=roots[0].policy.commercial_payload.plan
        try:
            validate_tariff(payload)
            GSTConfig(str(gst[0].profile.pk),gst[0].profile.profile_version,
                gst[0].profile.seller_tax_ref,gst[0].profile.legal_decision_ref,
                gst[0].profile.status,gst[0].profile.tax_classes)
            classes=set()
            for profile in payload['profiles'].values():
                classes.add(profile['charging']['tax_class'])
                for x in ('visit','distance'):
                    if x in profile:classes.add(profile[x]['tax_class'])
                for extra in profile.get('extras',[]):classes.add(extra['tax_class'])
            if classes - set(gst[0].profile.tax_classes):
                blockers.append('GST_CLASS_MISSING')
        except CommercialError as exc:blockers.append(exc.code)
    current=CommercialZoneChannel.objects.filter(zone=zone,channel=channel).first()
    return {'zone_id':str(zone.pk),'channel':channel,'enabled':bool(current and current.enabled),
            'configuration_ready':not blockers,'blockers':blockers,
            'default_tariff_assignment_id':str(roots[0].pk) if len(roots)==1 else None,
            'gst_assignment_id':str(gst[0].pk) if len(gst)==1 else None,
            'note':'Readiness does not prove physical charger, route, duty or legal certification'}
