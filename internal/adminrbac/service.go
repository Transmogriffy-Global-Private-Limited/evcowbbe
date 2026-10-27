package adminrbac

import (
	"context"
	"errors"
	"sort"
	"strings"
	"unicode"
	"unicode/utf8"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
)

var (
	ErrForbidden = errors.New("administrative capability required")
	ErrInvalid   = errors.New("invalid RBAC input")
	ErrNotFound  = errors.New("admin principal not found")
	ErrConflict  = errors.New("RBAC conflict or lockout guard")
)

type Service struct{ Pool *pgxpool.Pool }
type Snapshot struct {
	AccountID             string            `json:"account_id"`
	Roles                 []string          `json:"roles"`
	Overrides             map[string]string `json:"overrides"`
	EffectiveCapabilities []string          `json:"effective_capabilities"`
}

type querier interface {
	Query(context.Context, string, ...any) (pgx.Rows, error)
}

func snapshot(ctx context.Context, q querier, account string) (Snapshot, error) {
	var result = Snapshot{AccountID: account, Roles: []string{}, Overrides: map[string]string{}}
	rows, err := q.Query(ctx, `SELECT role_key FROM admin_role_assignments WHERE account_id=$1`, account)
	if err != nil {
		return Snapshot{}, err
	}
	for rows.Next() {
		var role string
		if err = rows.Scan(&role); err != nil {
			break
		}
		result.Roles = append(result.Roles, role)
	}
	if err == nil {
		err = rows.Err()
	}
	rows.Close()
	if err != nil {
		return Snapshot{}, err
	}
	rows, err = q.Query(ctx, `SELECT capability,decision FROM admin_capability_overrides WHERE account_id=$1`, account)
	if err != nil {
		return Snapshot{}, err
	}
	for rows.Next() {
		var cap, decision string
		if err = rows.Scan(&cap, &decision); err != nil {
			break
		}
		result.Overrides[cap] = decision
	}
	if err == nil {
		err = rows.Err()
	}
	rows.Close()
	if err != nil {
		return Snapshot{}, err
	}
	sort.Strings(result.Roles)
	result.EffectiveCapabilities = Evaluate(result.Roles, result.Overrides)
	return result, nil
}

func (s Service) Snapshot(ctx context.Context, account string) (Snapshot, error) {
	return snapshot(ctx, s.Pool, account)
}
func (s Service) Require(ctx context.Context, account, capability string) error {
	if !ValidCapability(capability) {
		return ErrForbidden
	}
	v, err := s.Snapshot(ctx, account)
	if err != nil {
		return err
	}
	for _, cap := range v.EffectiveCapabilities {
		if cap == capability {
			return nil
		}
	}
	return ErrForbidden
}

func validReason(reason string) bool {
	clean := strings.TrimSpace(reason)
	if !utf8.ValidString(clean) || utf8.RuneCountInString(clean) < 8 || utf8.RuneCountInString(clean) > 500 {
		return false
	}
	for _, r := range clean {
		if unicode.IsControl(r) {
			return false
		}
	}
	return true
}

// mutate serializes RBAC writes, rechecks actor's effective capability inside
// the transaction, protects the final FULL_ADMIN assignment, and records a
// non-secret reason for every privilege change.
func (s Service) mutate(ctx context.Context, actor, target, reason, operation, subject, value string) error {
	if actor == target && actor != "" {
		return ErrForbidden
	}
	if !validReason(reason) || actor == "" || target == "" {
		return ErrInvalid
	}
	tx, err := s.Pool.Begin(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback(ctx)
	if _, err = tx.Exec(ctx, `SELECT pg_advisory_xact_lock(1437519,20261027)`); err != nil {
		return err
	}
	var active bool
	err = tx.QueryRow(ctx, `SELECT EXISTS(SELECT 1 FROM principals p JOIN accounts a ON a.id=p.account_id
 WHERE p.account_id=$1 AND p.plane='admin' AND p.status='ACTIVE' AND a.disabled_at IS NULL AND a.email_verified_at IS NOT NULL)`, actor).Scan(&active)
	if err != nil {
		return err
	}
	if !active {
		return ErrForbidden
	}
	actorCaps, err := snapshot(ctx, tx, actor)
	if err != nil {
		return err
	}
	allowed := false
	for _, cap := range actorCaps.EffectiveCapabilities {
		if cap == "system.settings.manage" {
			allowed = true
			break
		}
	}
	if !allowed {
		return ErrForbidden
	}
	err = tx.QueryRow(ctx, `SELECT EXISTS(SELECT 1 FROM principals p JOIN accounts a ON a.id=p.account_id
 WHERE p.account_id=$1 AND p.plane='admin' AND p.status='ACTIVE' AND a.disabled_at IS NULL)`, target).Scan(&active)
	if err != nil {
		return err
	}
	if !active {
		return ErrNotFound
	}
	switch operation {
	case "ROLE_ADD":
		if !ValidRole(subject) {
			return ErrInvalid
		}
		tag, e := tx.Exec(ctx, `INSERT INTO admin_role_assignments(account_id,role_key,assigned_by) VALUES($1,$2,$3) ON CONFLICT(account_id,role_key) DO NOTHING`, target, subject, actor)
		if e != nil {
			return e
		}
		if tag.RowsAffected() == 0 {
			return ErrConflict
		}
	case "ROLE_REMOVE":
		if !ValidRole(subject) {
			return ErrInvalid
		}
		if subject == "FULL_ADMIN" {
			var count int
			if err = tx.QueryRow(ctx, `SELECT COUNT(*) FROM admin_role_assignments r JOIN principals p ON p.account_id=r.account_id AND p.plane='admin' JOIN accounts a ON a.id=p.account_id
 WHERE r.role_key='FULL_ADMIN' AND p.status='ACTIVE' AND a.disabled_at IS NULL AND a.email_verified_at IS NOT NULL`).Scan(&count); err != nil {
				return err
			}
			if count <= 1 {
				return ErrConflict
			}
		}
		tag, e := tx.Exec(ctx, `DELETE FROM admin_role_assignments WHERE account_id=$1 AND role_key=$2`, target, subject)
		if e != nil {
			return e
		}
		if tag.RowsAffected() == 0 {
			return ErrConflict
		}
	case "OVERRIDE_SET":
		if !ValidCapability(subject) || (value != "ALLOW" && value != "DENY") {
			return ErrInvalid
		}
		_, err = tx.Exec(ctx, `INSERT INTO admin_capability_overrides(account_id,capability,decision,changed_by)
 VALUES($1,$2,$3,$4) ON CONFLICT(account_id,capability) DO UPDATE SET decision=EXCLUDED.decision,changed_by=EXCLUDED.changed_by,changed_at=now()`, target, subject, value, actor)
	case "OVERRIDE_REMOVE":
		if !ValidCapability(subject) {
			return ErrInvalid
		}
		tag, e := tx.Exec(ctx, `DELETE FROM admin_capability_overrides WHERE account_id=$1 AND capability=$2`, target, subject)
		if e != nil {
			return e
		}
		if tag.RowsAffected() == 0 {
			return ErrConflict
		}
	default:
		return ErrInvalid
	}
	if err != nil {
		return err
	}
	// Never commit a privilege edit that strands every active administrator
	// without the ability to repair the permission registry.
	rows, e := tx.Query(ctx, `SELECT p.account_id::text FROM principals p JOIN accounts a ON a.id=p.account_id
 WHERE p.plane='admin' AND p.status='ACTIVE' AND a.disabled_at IS NULL AND a.email_verified_at IS NOT NULL`)
	if e != nil {
		return e
	}
	var admins []string
	for rows.Next() {
		var id string
		if e = rows.Scan(&id); e != nil {
			break
		}
		admins = append(admins, id)
	}
	if e == nil {
		e = rows.Err()
	}
	rows.Close()
	if e != nil {
		return e
	}
	managerFound := false
	for _, id := range admins {
		v, e := snapshot(ctx, tx, id)
		if e != nil {
			return e
		}
		for _, cap := range v.EffectiveCapabilities {
			if cap == "system.settings.manage" {
				managerFound = true
				break
			}
		}
		if managerFound {
			break
		}
	}
	if !managerFound {
		return ErrConflict
	}
	if _, err = tx.Exec(ctx, `INSERT INTO admin_rbac_audit(actor_id,target_id,operation,subject,reason) VALUES($1,$2,$3,$4,$5)`, actor, target, operation, subject, strings.TrimSpace(reason)); err != nil {
		return err
	}
	if _, err = tx.Exec(ctx, `INSERT INTO security_audit(account_id,plane,event) VALUES($1,'admin','ADMIN_RBAC_CHANGED')`, target); err != nil {
		return err
	}
	return tx.Commit(ctx)
}

func (s Service) AddRole(ctx context.Context, actor, target, role, reason string) error {
	return s.mutate(ctx, actor, target, reason, "ROLE_ADD", role, "")
}
func (s Service) RemoveRole(ctx context.Context, actor, target, role, reason string) error {
	return s.mutate(ctx, actor, target, reason, "ROLE_REMOVE", role, "")
}
func (s Service) SetOverride(ctx context.Context, actor, target, cap, decision, reason string) error {
	return s.mutate(ctx, actor, target, reason, "OVERRIDE_SET", cap, decision)
}
func (s Service) RemoveOverride(ctx context.Context, actor, target, cap, reason string) error {
	return s.mutate(ctx, actor, target, reason, "OVERRIDE_REMOVE", cap, "")
}
