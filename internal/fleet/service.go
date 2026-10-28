// Package fleet owns immutable identities and versioned master data for
// service vehicles, chargers and connectors. It never infers HAL or duty truth.
package fleet

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/json"
	"errors"
	"fmt"
	"sort"
	"strconv"
	"strings"
	"time"
	"unicode"
	"unicode/utf8"

	"github.com/Transmogriffy-Global-Private-Limited/evcowbbe/internal/security"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgconn"
	"github.com/jackc/pgx/v5/pgxpool"
)

var (
	ErrInvalid  = errors.New("fleet request validation failed")
	ErrNotFound = errors.New("fleet resource not found")
	ErrConflict = errors.New("fleet state conflict")
	ErrVersion  = errors.New("fleet version mismatch")
	ErrKeyReuse = errors.New("idempotency key reused with different request")
)

type Service struct{ Pool *pgxpool.Pool }
type Field struct {
	Type     string
	Max      int
	Required bool
	Nullable bool
	Values   []string
}
type Resource struct {
	Table, AuditKind string
	Fields           map[string]Field
}

var vehicle = Resource{"service_vehicles", "VEHICLE", map[string]Field{
	"asset_tag":                {Type: "tag", Required: true, Max: 64},
	"registration_number":      {Type: "tag", Nullable: true, Max: 32},
	"make":                     {Type: "text", Required: true, Max: 120},
	"model":                    {Type: "text", Required: true, Max: 120},
	"variant":                  {Type: "text", Nullable: true, Max: 120},
	"vehicle_type":             {Type: "text", Nullable: true, Max: 80},
	"ownership_type":           {Type: "text", Nullable: true, Max: 80},
	"registration_valid_until": {Type: "date", Nullable: true},
	"insurance_valid_until":    {Type: "date", Nullable: true},
	"inspection_status":        {Type: "enum", Values: []string{"UNKNOWN", "VALID", "FAILED", "EXPIRED"}},
	"notes":                    {Type: "text", Nullable: true, Max: 4000},
}}
var charger = Resource{"chargers", "CHARGER", map[string]Field{
	"asset_tag":        {Type: "tag", Required: true, Max: 64},
	"serial_number":    {Type: "tag", Nullable: true, Max: 120},
	"manufacturer":     {Type: "text", Required: true, Max: 120},
	"model":            {Type: "text", Required: true, Max: 120},
	"rated_power_w":    {Type: "positive_int", Nullable: true},
	"protocol_family":  {Type: "text", Nullable: true, Max: 80},
	"protocol_version": {Type: "text", Nullable: true, Max: 80},
	"hal_identity":     {Type: "text", Nullable: true, Max: 150},
	"notes":            {Type: "text", Nullable: true, Max: 4000},
}}
var connector = Resource{"charger_connectors", "CONNECTOR", map[string]Field{
	"connector_number": {Type: "positive_int", Required: true},
	"connector_type":   {Type: "tag", Required: true, Max: 64},
	"max_power_w":      {Type: "positive_int", Required: true},
	"supports_ac":      {Type: "bool", Required: true},
	"supports_dc":      {Type: "bool", Required: true},
	"is_active":        {Type: "bool"},
}}

func ValidateConnector(payload []byte, create bool) (map[string]any, error) {
	return ValidateFields(connector, payload, create)
}

func Kind(name string) (Resource, bool) {
	switch name {
	case "vehicles":
		return vehicle, true
	case "chargers":
		return charger, true
	}
	return Resource{}, false
}
func uuid() (string, error) { return security.UUID() }

func ValidReason(v string) bool {
	if utf8.RuneCountInString(strings.TrimSpace(v)) < 8 || utf8.RuneCountInString(v) > 500 || !utf8.ValidString(v) {
		return false
	}
	for _, c := range v {
		if unicode.IsControl(c) {
			return false
		}
	}
	return true
}

// ValidateFields rejects unknown and read-only fields, normalizes exact schema
// types and prevents SQL column injection by limiting fields to this catalog.
func ValidateFields(resource Resource, payload []byte, create bool) (map[string]any, error) {
	if len(payload) == 0 || len(payload) > 32768 {
		return nil, ErrInvalid
	}
	var raw map[string]json.RawMessage
	// Unmarshal rejects trailing JSON; explicit field lookup below rejects
	// unknown columns, including fields controlled exclusively by the server.
	if err := json.Unmarshal(payload, &raw); err != nil || raw == nil {
		return nil, ErrInvalid
	}
	result := make(map[string]any, len(raw))
	for field, value := range raw {
		spec, ok := resource.Fields[field]
		if !ok {
			return nil, ErrInvalid
		}
		if string(value) == "null" {
			if !spec.Nullable {
				return nil, ErrInvalid
			}
			result[field] = nil
			continue
		}
		switch spec.Type {
		case "text", "tag", "enum", "date":
			var v string
			if json.Unmarshal(value, &v) != nil {
				return nil, ErrInvalid
			}
			v = strings.TrimSpace(v)
			if spec.Type == "tag" || spec.Type == "enum" {
				v = strings.ToUpper(v)
			}
			if v == "" || !utf8.ValidString(v) || (spec.Max > 0 && utf8.RuneCountInString(v) > spec.Max) {
				return nil, ErrInvalid
			}
			if spec.Type == "tag" && utf8.RuneCountInString(v) < 2 {
				return nil, ErrInvalid
			}
			for _, c := range v {
				if unicode.IsControl(c) {
					return nil, ErrInvalid
				}
			}
			if spec.Type == "tag" {
				for _, c := range v {
					if !(c >= 'A' && c <= 'Z' || c >= '0' && c <= '9' || c == '-' || c == '_' || c == '/' || c == '.' || c == ' ') {
						return nil, ErrInvalid
					}
				}
			}
			if spec.Type == "date" {
				if _, err := time.Parse("2006-01-02", v); err != nil {
					return nil, ErrInvalid
				}
			}
			if spec.Type == "enum" {
				valid := false
				for _, allowed := range spec.Values {
					if v == allowed {
						valid = true
					}
				}
				if !valid {
					return nil, ErrInvalid
				}
			}
			result[field] = v
		case "positive_int":
			var v int64
			if json.Unmarshal(value, &v) != nil || v <= 0 || v > 2147483647 {
				return nil, ErrInvalid
			}
			result[field] = v
		case "bool":
			var v bool
			if json.Unmarshal(value, &v) != nil {
				return nil, ErrInvalid
			}
			result[field] = v
		default:
			return nil, ErrInvalid
		}
	}
	if create {
		for name, spec := range resource.Fields {
			if spec.Required {
				if _, ok := result[name]; !ok {
					return nil, ErrInvalid
				}
			}
		}
	} else if len(result) == 0 {
		return nil, ErrInvalid
	}
	if resource.Table == "charger_connectors" {
		if ac, ok1 := result["supports_ac"].(bool); ok1 {
			if dc, ok2 := result["supports_dc"].(bool); ok2 && !ac && !dc {
				return nil, ErrInvalid
			}
		}
	}
	return result, nil
}

func IntegrityError(err error) error {
	var pg *pgconn.PgError
	if errors.As(err, &pg) {
		switch pg.Code {
		case "23505", "23514", "23P01", "23503":
			return ErrConflict
		}
	}
	return err
}

func keyDigest(operation string, body []byte) []byte {
	h := sha256.Sum256(append([]byte(operation+"\n"), body...))
	return h[:]
}
func validKey(k string) bool {
	if len(k) < 16 || len(k) > 128 {
		return false
	}
	for _, c := range k {
		if c < 33 || c > 126 {
			return false
		}
	}
	return true
}

// RunOnce is a reusable PostgreSQL transactional idempotency primitive.
// The receipt becomes visible atomically with the mutation. Duplicate concurrent
// requests block on the unique key and replay the committed response. A key
// mismatch never runs the mutation a second time.
func (s Service) RunOnce(ctx context.Context, actor, key, op string, body []byte, run func(pgx.Tx) (json.RawMessage, int, error)) (json.RawMessage, int, error) {
	if !validKey(key) {
		return nil, 0, ErrInvalid
	}
	digest := keyDigest(op, body)
	tx, err := s.Pool.Begin(ctx)
	if err != nil {
		return nil, 0, err
	}
	defer tx.Rollback(ctx)
	tag, err := tx.Exec(ctx, `INSERT INTO mutation_idempotency_receipts(actor_id,request_key,request_digest,response_status,response_body)
 VALUES($1,$2,$3,0,'{}') ON CONFLICT DO NOTHING`, actor, key, digest)
	if err != nil {
		return nil, 0, err
	}
	if tag.RowsAffected() == 0 {
		var oldDigest []byte
		var status int
		var stored []byte
		err = tx.QueryRow(ctx, `SELECT request_digest,response_status,response_body FROM mutation_idempotency_receipts WHERE actor_id=$1 AND request_key=$2`, actor, key).Scan(&oldDigest, &status, &stored)
		if err != nil {
			return nil, 0, err
		}
		if !bytes.Equal(oldDigest, digest) {
			return nil, 0, ErrKeyReuse
		}
		if status < 200 || status > 299 {
			return nil, 0, ErrConflict
		}
		return json.RawMessage(stored), status, nil
	}
	out, status, err := run(tx)
	if err != nil {
		return nil, 0, err
	}
	if status < 200 || status > 299 || !json.Valid(out) {
		return nil, 0, ErrInvalid
	}
	_, err = tx.Exec(ctx, `UPDATE mutation_idempotency_receipts SET response_status=$3,response_body=$4 WHERE actor_id=$1 AND request_key=$2`, actor, key, status, out)
	if err != nil {
		return nil, 0, err
	}
	if err = tx.Commit(ctx); err != nil {
		return nil, 0, err
	}
	return out, status, nil
}

func fetch(ctx context.Context, q interface {
	QueryRow(context.Context, string, ...any) pgx.Row
}, table, id string) (json.RawMessage, error) {
	var data []byte
	err := q.QueryRow(ctx, "SELECT to_jsonb(t) FROM "+table+" t WHERE id=$1", id).Scan(&data)
	if errors.Is(err, pgx.ErrNoRows) {
		return nil, ErrNotFound
	}
	return json.RawMessage(data), err
}
func (s Service) Get(ctx context.Context, r Resource, id string) (json.RawMessage, error) {
	return fetch(ctx, s.Pool, r.Table, id)
}

func audit(ctx context.Context, tx pgx.Tx, actor, kind, id, action string, version int64, reason string, before, after json.RawMessage) error {
	_, err := tx.Exec(ctx, `INSERT INTO fleet_asset_audit(actor_id,asset_kind,asset_id,action,version,reason,before_value,after_value) VALUES($1,$2,$3,$4,$5,$6,$7,$8)`, actor, kind, id, action, version, reason, before, after)
	return err
}

func (s Service) Create(ctx context.Context, tx pgx.Tx, r Resource, actor string, input map[string]any, chargerID string) (json.RawMessage, error) {
	id, err := uuid()
	if err != nil {
		return nil, err
	}
	columns := []string{"id", "created_by", "updated_by"}
	args := []any{id, actor, actor}
	if chargerID != "" {
		columns = append(columns, "charger_id")
		args = append(args, chargerID)
	}
	keys := make([]string, 0, len(input))
	for k := range input {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	for _, k := range keys {
		columns = append(columns, k)
		args = append(args, input[k])
	}
	marks := make([]string, len(args))
	for i := range marks {
		marks[i] = "$" + strconv.Itoa(i+1)
	}
	_, err = tx.Exec(ctx, "INSERT INTO "+r.Table+" ("+strings.Join(columns, ",")+") VALUES ("+strings.Join(marks, ",")+")", args...)
	if err != nil {
		return nil, IntegrityError(err)
	}
	after, err := fetch(ctx, tx, r.Table, id)
	if err != nil {
		return nil, err
	}
	if err = audit(ctx, tx, actor, r.AuditKind, id, "CREATE", 1, "", nil, after); err != nil {
		return nil, err
	}
	return after, nil
}

func (s Service) Patch(ctx context.Context, tx pgx.Tx, r Resource, actor, id string, version int64, input map[string]any, chargerID string) (json.RawMessage, error) {
	var current int64
	lock := "SELECT version FROM " + r.Table + " WHERE id=$1"
	args := []any{id}
	if chargerID != "" {
		lock += " AND charger_id=$2"
		args = append(args, chargerID)
	}
	lock += " FOR UPDATE"
	err := tx.QueryRow(ctx, lock, args...).Scan(&current)
	if errors.Is(err, pgx.ErrNoRows) {
		return nil, ErrNotFound
	}
	if err != nil {
		return nil, err
	}
	if current != version {
		return nil, ErrVersion
	}
	before, err := fetch(ctx, tx, r.Table, id)
	if err != nil {
		return nil, err
	}
	keys := make([]string, 0, len(input))
	for k := range input {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	clauses := make([]string, 0, len(keys))
	different := make([]string, 0, len(keys))
	params := []any{actor, id, version}
	for _, k := range keys {
		params = append(params, input[k])
		n := strconv.Itoa(len(params))
		clauses = append(clauses, k+"=$"+n)
		different = append(different, k+" IS DISTINCT FROM $"+n)
	}
	clauses = append(clauses, "version=version+1", "updated_at=now()")
	clauses = append(clauses, "updated_by=$1")
	query := "UPDATE " + r.Table + " SET " + strings.Join(clauses, ",") + " WHERE id=$2 AND version=$3 AND (" + strings.Join(different, " OR ") + ") RETURNING version"
	var newVersion int64
	err = tx.QueryRow(ctx, query, params...).Scan(&newVersion)
	if errors.Is(err, pgx.ErrNoRows) {
		return fetch(ctx, tx, r.Table, id)
	}
	if err != nil {
		return nil, IntegrityError(err)
	}
	after, err := fetch(ctx, tx, r.Table, id)
	if err != nil {
		return nil, err
	}
	if err = audit(ctx, tx, actor, r.AuditKind, id, "UPDATE", newVersion, "", before, after); err != nil {
		return nil, err
	}
	return after, nil
}

type Page struct {
	Items      []json.RawMessage `json:"items"`
	NextCursor string            `json:"next_cursor,omitempty"`
}
type Filter struct {
	Limit                int
	After, Status, Query string
	Active               *bool
}

func (s Service) List(ctx context.Context, r Resource, f Filter) (Page, error) {
	if f.Limit == 0 {
		f.Limit = 50
	}
	if f.Limit < 1 || f.Limit > 100 || len(f.Query) > 120 {
		return Page{}, ErrInvalid
	}
	if f.Status != "" && f.Status != "AVAILABLE" && f.Status != "OUT_OF_SERVICE" && f.Status != "RETIRED" {
		return Page{}, ErrInvalid
	}
	args := []any{}
	wheres := []string{}
	if f.After != "" {
		var date time.Time
		err := s.Pool.QueryRow(ctx, "SELECT created_at FROM "+r.Table+" WHERE id=$1", f.After).Scan(&date)
		if errors.Is(err, pgx.ErrNoRows) {
			return Page{}, ErrInvalid
		}
		if err != nil {
			return Page{}, err
		}
		args = append(args, date, f.After)
		wheres = append(wheres, fmt.Sprintf("(created_at,id)>($%d,$%d)", len(args)-1, len(args)))
	}
	if f.Status != "" {
		args = append(args, f.Status)
		wheres = append(wheres, "operational_status=$"+strconv.Itoa(len(args)))
	}
	if f.Active != nil {
		args = append(args, *f.Active)
		wheres = append(wheres, "is_active=$"+strconv.Itoa(len(args)))
	}
	if f.Query != "" {
		args = append(args, "%"+strings.ReplaceAll(strings.ReplaceAll(strings.ReplaceAll(f.Query, "\\", "\\\\"), "%", "\\%"), "_", "\\_")+"%")
		wheres = append(wheres, "(asset_tag ILIKE $"+strconv.Itoa(len(args))+" ESCAPE '\\' OR model ILIKE $"+strconv.Itoa(len(args))+" ESCAPE '\\')")
	}
	sql := "SELECT id,to_jsonb(t) FROM " + r.Table + " t"
	if len(wheres) > 0 {
		sql += " WHERE " + strings.Join(wheres, " AND ")
	}
	args = append(args, f.Limit+1)
	sql += " ORDER BY created_at,id LIMIT $" + strconv.Itoa(len(args))
	rows, err := s.Pool.Query(ctx, sql, args...)
	if err != nil {
		return Page{}, err
	}
	defer rows.Close()
	page := Page{Items: []json.RawMessage{}}
	last := ""
	for rows.Next() {
		var id string
		var data []byte
		if err = rows.Scan(&id, &data); err != nil {
			return Page{}, err
		}
		if len(page.Items) < f.Limit {
			page.Items = append(page.Items, data)
			last = id
		} else {
			page.NextCursor = last
			break
		}
	}
	if err = rows.Err(); err != nil {
		return Page{}, err
	}
	return page, nil
}

func (s Service) History(ctx context.Context, kind, id string) ([]json.RawMessage, error) {
	if kind != "VEHICLE" && kind != "CHARGER" {
		return nil, ErrInvalid
	}
	// Include subordinate pairing and maintenance events. A listing that only
	// showed direct metadata patches would hide important physical-intent history.
	rows, err := s.Pool.Query(ctx, `SELECT to_jsonb(a) FROM (
 SELECT a.id,a.actor_id,a.asset_kind,a.asset_id,a.action,a.version,a.reason,a.before_value,a.after_value,a.created_at
 FROM fleet_asset_audit a
 LEFT JOIN vehicle_charger_pairings p ON a.asset_kind='PAIRING' AND a.asset_id=p.id
 LEFT JOIN asset_maintenance_periods m ON a.asset_kind='MAINTENANCE' AND a.asset_id=m.id
 WHERE (a.asset_kind=$1 AND a.asset_id=$2)
 OR ($1='VEHICLE' AND (p.vehicle_id=$2 OR m.vehicle_id=$2))
 OR ($1='CHARGER' AND (p.charger_id=$2 OR m.charger_id=$2))
 ORDER BY a.id DESC LIMIT 100) a`, kind, id)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	events := []json.RawMessage{}
	for rows.Next() {
		var raw []byte
		if err = rows.Scan(&raw); err != nil {
			return nil, err
		}
		events = append(events, raw)
	}
	return events, rows.Err()
}
