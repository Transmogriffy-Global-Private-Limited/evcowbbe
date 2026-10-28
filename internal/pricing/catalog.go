package pricing

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"time"

	"github.com/Transmogriffy-Global-Private-Limited/evcowbbe/internal/fleet"
	"github.com/Transmogriffy-Global-Private-Limited/evcowbbe/internal/security"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
)

var ErrNotFound = errors.New("pricing policy not found")
var ErrStateConflict = errors.New("pricing policy lifecycle conflict")
var ErrVersionConflict = errors.New("pricing policy state version conflict")

type Catalog struct{ Pool *pgxpool.Pool }

// DraftRequest is an immutable proposed revision. Updates always create a
// new policy version rather than changing price terms under a live quote.
type DraftRequest struct {
	PolicyKey string     `json:"policy_key"`
	Version   int64      `json:"policy_version"`
	Scope     Scope      `json:"scope"`
	StartsAt  *time.Time `json:"starts_at"`
	EndsAt    *time.Time `json:"ends_at"`
	Currency  string     `json:"currency"`
	Rules     []Rule     `json:"rules"`
	Reason    string     `json:"reason"`
}

type TransitionRequest struct {
	Reason string `json:"reason"`
}

func StrictJSON(raw []byte, target any) error {
	if len(raw) < 2 || len(raw) > 65536 {
		return ErrInvalidPolicy
	}
	trimmed := bytes.TrimSpace(raw)
	if len(trimmed) < 2 || trimmed[0] != '{' {
		return ErrInvalidPolicy
	}
	// Go's default JSON decoder accepts repeated object fields. For financial
	// policy approval this would make a visually reviewed value disagree with
	// the effective last value, so reject duplicates at every nesting level.
	unique := json.NewDecoder(bytes.NewReader(raw))
	if uniqueJSONObject(unique) != nil || unique.Decode(new(any)) != io.EOF {
		return ErrInvalidPolicy
	}
	decoder := json.NewDecoder(bytes.NewReader(raw))
	decoder.DisallowUnknownFields()
	if decoder.Decode(target) != nil || decoder.Decode(new(any)) != io.EOF {
		return ErrInvalidPolicy
	}
	return nil
}

func uniqueJSONObject(decoder *json.Decoder) error {
	token, err := decoder.Token()
	if err != nil {
		return err
	}
	delim, ok := token.(json.Delim)
	if !ok {
		return nil
	}
	switch delim {
	case '{':
		seen := map[string]bool{}
		for decoder.More() {
			field, err := decoder.Token()
			if err != nil {
				return err
			}
			name, ok := field.(string)
			if !ok || seen[name] {
				return ErrInvalidPolicy
			}
			seen[name] = true
			if err := uniqueJSONObject(decoder); err != nil {
				return err
			}
		}
	case '[':
		for decoder.More() {
			if err := uniqueJSONObject(decoder); err != nil {
				return err
			}
		}
	default:
		return ErrInvalidPolicy
	}
	end, err := decoder.Token()
	if err != nil {
		return err
	}
	want := json.Delim('}')
	if delim == '[' {
		want = json.Delim(']')
	}
	if end != want {
		return ErrInvalidPolicy
	}
	return nil
}

func validReason(reason string) bool {
	return fleet.ValidReason(reason)
}

func (r DraftRequest) Validate() error {
	if !validID(r.PolicyKey) || !validScope(r.Scope) || !validReason(r.Reason) ||
		validateInterval(r.StartsAt, r.EndsAt) != nil {
		return ErrInvalidPolicy
	}
	return Validate(Terms{PolicyID: r.PolicyKey, PolicyVersion: r.Version, Currency: r.Currency, Rules: r.Rules})
}

func readRecord(ctx context.Context, q interface {
	QueryRow(context.Context, string, ...any) pgx.Row
}, id string) (json.RawMessage, error) {
	var data []byte
	err := q.QueryRow(ctx, `SELECT to_jsonb(p) FROM pricing_policies p WHERE id=$1`, id).Scan(&data)
	if errors.Is(err, pgx.ErrNoRows) {
		return nil, ErrNotFound
	}
	return data, err
}

func auditPolicy(ctx context.Context, tx pgx.Tx, id, actor, action, reason string, before, after json.RawMessage) error {
	_, err := tx.Exec(ctx, `INSERT INTO pricing_policy_audit(policy_id,actor_id,action,reason,before_value,after_value)
	 VALUES($1,$2,$3,$4,$5,$6)`, id, actor, action, reason, before, after)
	return err
}

func (c Catalog) CreateDraft(ctx context.Context, tx pgx.Tx, actor string, r DraftRequest) (json.RawMessage, error) {
	if err := r.Validate(); err != nil {
		return nil, err
	}
	id, err := security.UUID()
	if err != nil {
		return nil, err
	}
	rules, err := json.Marshal(r.Rules)
	if err != nil {
		return nil, err
	}
	_, err = tx.Exec(ctx, `INSERT INTO pricing_policies
	 (id,policy_key,policy_version,scope_kind,scope_key,starts_at,ends_at,currency,rules,reason,created_by)
	 VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)`,
		id, r.PolicyKey, r.Version, r.Scope.Kind, r.Scope.Key, r.StartsAt, r.EndsAt, r.Currency, rules, r.Reason, actor)
	if err != nil {
		return nil, fleet.IntegrityError(err)
	}
	after, err := readRecord(ctx, tx, id)
	if err != nil {
		return nil, err
	}
	if err := auditPolicy(ctx, tx, id, actor, "DRAFT_CREATED", r.Reason, nil, after); err != nil {
		return nil, err
	}
	return after, nil
}

func (c Catalog) Get(ctx context.Context, id string) (json.RawMessage, error) {
	return readRecord(ctx, c.Pool, id)
}

func (c Catalog) List(ctx context.Context, state string, limit int) (json.RawMessage, error) {
	if limit < 1 || limit > 100 || state != "" && state != "DRAFT" && state != "PUBLISHED" && state != "WITHDRAWN" {
		return nil, ErrInvalidPolicy
	}
	var raw []byte
	err := c.Pool.QueryRow(ctx, `SELECT coalesce(jsonb_agg(to_jsonb(p) ORDER BY p.created_at DESC,p.id DESC),'[]'::jsonb)
	 FROM (SELECT * FROM pricing_policies WHERE ($1='' OR lifecycle=$1) ORDER BY created_at DESC,id DESC LIMIT $2) p`, state, limit).Scan(&raw)
	return raw, err
}

func (c Catalog) History(ctx context.Context, id string) (json.RawMessage, error) {
	if _, err := c.Get(ctx, id); err != nil {
		return nil, err
	}
	var raw []byte
	err := c.Pool.QueryRow(ctx, `SELECT coalesce(jsonb_agg(to_jsonb(h) ORDER BY h.id DESC),'[]'::jsonb)
	 FROM (SELECT * FROM pricing_policy_audit WHERE policy_id=$1 ORDER BY id DESC LIMIT 100) h`, id).Scan(&raw)
	return raw, err
}

func readLocked(ctx context.Context, tx pgx.Tx, id string) (json.RawMessage, string, int64, Scope, *time.Time, *time.Time, Terms, error) {
	var before, ruleData []byte
	var lifecycle, key, currency, scopeKind, scopeKey string
	var version, stateVersion int64
	var start, end *time.Time
	err := tx.QueryRow(ctx, `SELECT to_jsonb(p),lifecycle,state_version,policy_key,policy_version,
	 scope_kind,scope_key,starts_at,ends_at,currency,rules FROM pricing_policies p WHERE id=$1 FOR UPDATE`, id).
		Scan(&before, &lifecycle, &stateVersion, &key, &version, &scopeKind, &scopeKey, &start, &end, &currency, &ruleData)
	if errors.Is(err, pgx.ErrNoRows) {
		err = ErrNotFound
	}
	if err != nil {
		return nil, "", 0, Scope{}, nil, nil, Terms{}, err
	}
	var rules []Rule
	if err := json.Unmarshal(ruleData, &rules); err != nil {
		return nil, "", 0, Scope{}, nil, nil, Terms{}, err
	}
	return before, lifecycle, stateVersion, Scope{scopeKind, scopeKey}, start, end, Terms{key, version, currency, rules}, nil
}

func lockScope(ctx context.Context, tx pgx.Tx, scope Scope) error {
	_, err := tx.Exec(ctx, `SELECT pg_advisory_xact_lock(hashtextextended($1,0))`, scope.Kind+"/"+scope.Key)
	return err
}

// Publish serializes all concurrent publications in one exact scope, then
// verifies the entire enabled temporal topology before changing any state.
func (c Catalog) Publish(ctx context.Context, tx pgx.Tx, id, actor string, stateVersion int64, reason string) (json.RawMessage, error) {
	if !validReason(reason) || stateVersion < 1 {
		return nil, ErrInvalidPolicy
	}
	before, state, current, scope, start, end, terms, err := readLocked(ctx, tx, id)
	if err != nil {
		return nil, err
	}
	if current != stateVersion {
		return nil, ErrVersionConflict
	}
	if state != "DRAFT" {
		return nil, ErrStateConflict
	}
	if err = lockScope(ctx, tx, scope); err != nil {
		return nil, err
	}
	rows, err := tx.Query(ctx, `SELECT policy_key,policy_version,currency,rules,starts_at,ends_at
	 FROM pricing_policies WHERE scope_kind=$1 AND scope_key=$2 AND lifecycle='PUBLISHED'`, scope.Kind, scope.Key)
	if err != nil {
		return nil, err
	}
	list := []Policy{{Scope: scope, Active: true, Start: start, End: end, Terms: terms}}
	for rows.Next() {
		var key, currency string
		var version int64
		var jsonRules []byte
		var s, e *time.Time
		if err = rows.Scan(&key, &version, &currency, &jsonRules, &s, &e); err != nil {
			break
		}
		var rules []Rule
		if err = json.Unmarshal(jsonRules, &rules); err != nil {
			break
		}
		list = append(list, Policy{Scope: scope, Active: true, Start: s, End: e, Terms: Terms{key, version, currency, rules}})
	}
	if err == nil {
		err = rows.Err()
	}
	rows.Close()
	if err != nil {
		return nil, err
	}
	if err = ValidateTopology(list); err != nil {
		return nil, err
	}
	_, err = tx.Exec(ctx, `UPDATE pricing_policies SET lifecycle='PUBLISHED',state_version=state_version+1,published_at=now(),published_by=$2
	 WHERE id=$1`, id, actor)
	if err != nil {
		return nil, err
	}
	after, err := readRecord(ctx, tx, id)
	if err != nil {
		return nil, err
	}
	if err = auditPolicy(ctx, tx, id, actor, "PUBLISHED", reason, before, after); err != nil {
		return nil, err
	}
	return after, nil
}

// Withdraw affects only new resolutions; quotes and completed sessions must
// preserve their original price and tax snapshots even after withdrawal.
func (c Catalog) Withdraw(ctx context.Context, tx pgx.Tx, id, actor string, stateVersion int64, reason string) (json.RawMessage, error) {
	if !validReason(reason) || stateVersion < 1 {
		return nil, ErrInvalidPolicy
	}
	before, state, current, scope, _, _, _, err := readLocked(ctx, tx, id)
	if err != nil {
		return nil, err
	}
	if current != stateVersion {
		return nil, ErrVersionConflict
	}
	if state != "PUBLISHED" {
		return nil, ErrStateConflict
	}
	if err = lockScope(ctx, tx, scope); err != nil {
		return nil, err
	}
	_, err = tx.Exec(ctx, `UPDATE pricing_policies SET lifecycle='WITHDRAWN',state_version=state_version+1,withdrawn_at=now(),withdrawn_by=$2
	 WHERE id=$1`, id, actor)
	if err != nil {
		return nil, err
	}
	after, err := readRecord(ctx, tx, id)
	if err != nil {
		return nil, err
	}
	if err = auditPolicy(ctx, tx, id, actor, "WITHDRAWN", reason, before, after); err != nil {
		return nil, err
	}
	return after, nil
}

// LoadActive returns only active published tariff revisions for an internally
// authenticated scope decision. The API must never trust a client-supplied
// customer-group or location when constructing precedence.
func (c Catalog) LoadActive(ctx context.Context, scopes []Scope) ([]Policy, error) {
	if len(scopes) == 0 || len(scopes) > 16 {
		return nil, ErrInvalidPolicy
	}
	keys := map[Scope]bool{}
	for _, scope := range scopes {
		if !validScope(scope) || keys[scope] {
			return nil, ErrInvalidPolicy
		}
		keys[scope] = true
	}
	// This read is only a snapshot of committed rows. Publication's per-scope
	// advisory lock ensures the committed topology cannot be ambiguous.
	kinds := make([]string, 0, len(scopes))
	scopeKeys := make([]string, 0, len(scopes))
	for _, scope := range scopes {
		kinds = append(kinds, scope.Kind)
		scopeKeys = append(scopeKeys, scope.Key)
	}
	rows, err := c.Pool.Query(ctx, `SELECT policy_key,policy_version,currency,rules,scope_kind,scope_key,starts_at,ends_at
	 FROM pricing_policies WHERE lifecycle='PUBLISHED' AND scope_kind=ANY($1) AND scope_key=ANY($2)`, kinds, scopeKeys)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var result []Policy
	for rows.Next() {
		var key, currency, kind, scopeKey string
		var version int64
		var rulesData []byte
		var start, end *time.Time
		if err = rows.Scan(&key, &version, &currency, &rulesData, &kind, &scopeKey, &start, &end); err != nil {
			return nil, err
		}
		scope := Scope{kind, scopeKey}
		if !keys[scope] {
			continue
		}
		var rules []Rule
		if err = json.Unmarshal(rulesData, &rules); err != nil {
			return nil, err
		}
		result = append(result, Policy{Scope: scope, Active: true, Start: start, End: end, Terms: Terms{key, version, currency, rules}})
	}
	return result, rows.Err()
}

func (c Catalog) Resolve(ctx context.Context, scopes []Scope, at time.Time) (Policy, error) {
	policies, err := c.LoadActive(ctx, scopes)
	if err != nil {
		return Policy{}, fmt.Errorf("load active pricing: %w", err)
	}
	return Resolve(policies, scopes, at)
}

func (c Catalog) Check(ctx context.Context) error {
	if c.Pool == nil {
		return errors.New("pricing database unavailable")
	}
	return c.Pool.Ping(ctx)
}
