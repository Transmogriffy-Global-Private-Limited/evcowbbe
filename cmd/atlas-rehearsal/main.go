// Command atlas-rehearsal produces read-only, privacy-preserving fingerprints
// of disposable PostgreSQL databases and compares their schema/data snapshots.
// It never creates databases, executes DDL, or connects to the live app DB.
package main

import (
	"context"
	"crypto/sha256"
	"encoding/binary"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"hash"
	"io"
	"net/url"
	"os"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"time"

	"github.com/Transmogriffy-Global-Private-Limited/evcowbbe/internal/database"
	"github.com/jackc/pgx/v5"
)

const (
	connectionEnv   = "EVCOWBBE_ATLAS_REHEARSAL_URL"
	legacyUpConsent = "MIGRATE_ATLAS_REHEARSAL_TEST_DB_ONLY"
)

var (
	disposableName = regexp.MustCompile(`^evcowbbe_atlas_[a-z0-9_]+_test$`)
	bridgeTables   = map[string]bool{
		"principals":                    true,
		"auth_rate_limits":              true,
		"consumed_refresh_tokens":       true,
		"auth_action_tokens":            true,
		"admin_role_assignments":        true,
		"admin_capability_overrides":    true,
		"mutation_idempotency_receipts": true,
	}
	legacyChecksums = map[string]string{
		"1": "38b146ed4adb9ef6162843dc2adaede7896d8616cc39395d8b69a09eb85b635c",
		"2": "3d4fa76de2cd0c8326b9c659c50876ceccab2fb5bbe2542e6a318c75b422adbf",
		"3": "ea0947bd655fd442069541b8ff0e51b1276c60f64bdbe04b9750e94f798cfc41",
		"4": "4ea9c5776fb94d8b53b80253c84222efe40a14d11420943ae33e3c6a88e75b4a",
		"5": "362de922de83e0661d8117cc8fd65c6c9c17ff1a028cae10f480c079180e729d",
		"6": "093d0bcc31a419cd8400ceeb7c26f86a8000f037246f7a2f88d03861efaaee12",
	}
)

type tableDigest struct {
	Rows   int64  `json:"rows"`
	SHA256 string `json:"sha256"`
}

type snapshot struct {
	Database       string                 `json:"database"`
	ServerVersion  int                    `json:"server_version_num"`
	History        map[string]string      `json:"schema_migrations"`
	AtlasRevisions int64                  `json:"atlas_revisions"`
	Tables         map[string]tableDigest `json:"tables"`
	Catalog        []string               `json:"catalog"`
}

func main() {
	if err := run(os.Args[1:]); err != nil {
		fmt.Fprintln(os.Stderr, "ATLAS_REHEARSAL=FAIL:", err)
		os.Exit(1)
	}
}

func run(args []string) error {
	switch {
	case len(args) == 1 && args[0] == "snapshot":
		s, err := inspect(context.Background(), os.Getenv(connectionEnv), os.Getenv("DATABASE_URL"))
		if err != nil {
			return err
		}
		return json.NewEncoder(os.Stdout).Encode(s)
	case len(args) == 1 && args[0] == "legacy-up":
		return applyLegacy(context.Background(), os.Getenv(connectionEnv), os.Getenv("DATABASE_URL"), os.Getenv("EVCOWBBE_ATLAS_REHEARSAL_CONFIRM"))
	case len(args) == 2 && args[0] == "check-empty" && (args[1] == "fresh" || args[1] == "dev"):
		key := "EVCOWBBE_ATLAS_FRESH_URL"
		if args[1] == "dev" {
			key = "EVCOWBBE_ATLAS_DEV_URL"
		}
		if err := checkEmpty(context.Background(), os.Getenv(key), os.Getenv("DATABASE_URL"), args[1]); err != nil {
			return err
		}
		fmt.Printf("ATLAS_REHEARSAL_%s_EMPTY=PASS\n", strings.ToUpper(args[1]))
		return nil
	case len(args) == 3 && args[0] == "compare-data":
		before, after, err := readPair(args[1], args[2])
		if err != nil {
			return err
		}
		if err := compareData(before, after); err != nil {
			return err
		}
		fmt.Println("ATLAS_REHEARSAL_DATA=PASS")
		return nil
	case len(args) == 3 && args[0] == "compare-schema":
		left, right, err := readPair(args[1], args[2])
		if err != nil {
			return err
		}
		if err := compareSchema(left, right); err != nil {
			return err
		}
		fmt.Println("ATLAS_REHEARSAL_SCHEMA=PASS")
		return nil
	default:
		return errors.New("usage: atlas-rehearsal snapshot | legacy-up | check-empty fresh|dev | compare-data BEFORE.json AFTER.json | compare-schema LEFT.json RIGHT.json")
	}
}

func checkEmpty(parent context.Context, raw, appURL, kind string) error {
	name, err := checkTarget(raw, appURL)
	if err != nil {
		return err
	}
	if !strings.HasPrefix(name, "evcowbbe_atlas_"+kind+"_") {
		return fmt.Errorf("expected a disposable %s database, got a different target class", kind)
	}
	ctx, cancel := context.WithTimeout(parent, 15*time.Second)
	defer cancel()
	conn, err := pgx.Connect(ctx, raw)
	if err != nil {
		return errors.New("isolated PostgreSQL connection failed")
	}
	defer conn.Close(context.Background())
	var actual string
	var version, publicObjects int
	if err := conn.QueryRow(ctx, "SELECT current_database(), current_setting('server_version_num')::int").Scan(&actual, &version); err != nil {
		return err
	}
	if actual != name || version/10000 != 18 {
		return errors.New("unexpected database identity or PostgreSQL version")
	}
	if err := conn.QueryRow(ctx, `SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
	 WHERE n.nspname NOT IN ('pg_catalog','information_schema')
	 AND left(n.nspname,3) <> 'pg_'
	 AND c.relkind IN ('r','p','v','m','S','f')`).Scan(&publicObjects); err != nil {
		return err
	}
	if publicObjects != 0 {
		return errors.New("expected a truly empty disposable database before Atlas use")
	}
	return nil
}

func applyLegacy(parent context.Context, raw, appURL, consent string) error {
	name, err := checkTarget(raw, appURL)
	if err != nil {
		return err
	}
	if !strings.HasPrefix(name, "evcowbbe_atlas_populated_") {
		return errors.New("legacy-up may only target a disposable populated rehearsal database")
	}
	if consent != legacyUpConsent {
		return errors.New("explicit disposable migration consent is required")
	}
	pre, err := inspect(parent, raw, appURL)
	if err != nil {
		return fmt.Errorf("populated pre-migration read-only snapshot: %w", err)
	}
	if len(pre.History) != 3 || pre.Tables["accounts"].Rows == 0 {
		return errors.New("legacy-up requires a restored, populated 001-003 historical baseline; refusing other states")
	}
	ctx, cancel := context.WithTimeout(parent, 3*time.Minute)
	defer cancel()
	pool, err := database.Open(ctx, raw, 2)
	if err != nil {
		return err
	}
	defer pool.Close()
	var server int
	if err = pool.QueryRow(ctx, "SELECT current_setting('server_version_num')::int").Scan(&server); err != nil {
		return err
	}
	if server/10000 != 18 {
		return errors.New("expected PostgreSQL 18")
	}
	if err := database.Migrate(ctx, pool, "up"); err != nil {
		return err
	}
	if err := database.VerifySchema(ctx, pool); err != nil {
		return fmt.Errorf("post-migration ledger: %w", err)
	}
	fmt.Println("ATLAS_REHEARSAL_LEGACY_001_006=PASS")
	return nil
}

func checkTarget(raw, applicationURL string) (string, error) {
	if raw == "" || raw == applicationURL {
		return "", errors.New("missing or application database URL")
	}
	u, err := url.Parse(raw)
	if err != nil || (u.Scheme != "postgres" && u.Scheme != "postgresql") || u.Hostname() == "" || u.User == nil {
		return "", errors.New("expected an explicit PostgreSQL URL with host and user")
	}
	if u.RawQuery != "" && (u.Query().Get("dbname") != "" || u.Query().Get("database") != "") {
		return "", errors.New("database name override in URL is not permitted")
	}
	name := strings.TrimPrefix(u.EscapedPath(), "/")
	if !disposableName.MatchString(name) || strings.Contains(name, "%") || strings.Contains(name, "/") {
		return "", errors.New("refusing database outside evcowbbe_atlas_*_test naming boundary")
	}
	return name, nil
}

func inspect(parent context.Context, raw, appURL string) (*snapshot, error) {
	name, err := checkTarget(raw, appURL)
	if err != nil {
		return nil, err
	}
	ctx, cancel := context.WithTimeout(parent, 3*time.Minute)
	defer cancel()
	conn, err := pgx.Connect(ctx, raw)
	if err != nil {
		return nil, errors.New("disposable PostgreSQL connection failed")
	}
	defer conn.Close(context.Background())
	var actual string
	if err = conn.QueryRow(ctx, "SELECT current_database()").Scan(&actual); err != nil {
		return nil, err
	}
	if actual != name {
		return nil, errors.New("connected database name differs from verified URL")
	}
	tx, err := conn.BeginTx(ctx, pgx.TxOptions{IsoLevel: pgx.RepeatableRead, AccessMode: pgx.ReadOnly})
	if err != nil {
		return nil, err
	}
	defer tx.Rollback(context.Background())
	if _, err = tx.Exec(ctx, "SET LOCAL statement_timeout = '120s'"); err != nil {
		return nil, err
	}
	s := &snapshot{Database: name, History: make(map[string]string), Tables: make(map[string]tableDigest)}
	if err = tx.QueryRow(ctx, "SHOW server_version_num").Scan(&actual); err != nil {
		return nil, err
	}
	s.ServerVersion, err = strconv.Atoi(actual)
	if err != nil || s.ServerVersion/10000 != 18 {
		return nil, errors.New("PostgreSQL 18 required for this rehearsal")
	}
	if err = readHistory(ctx, tx, s); err != nil {
		return nil, err
	}
	if _, upgraded := s.History["6"]; upgraded {
		if err = verifyIdentityBridge(ctx, tx); err != nil {
			return nil, err
		}
	}
	if err = readCatalog(ctx, tx, s); err != nil {
		return nil, err
	}
	if err = readData(ctx, tx, s); err != nil {
		return nil, err
	}
	if err = tx.Commit(ctx); err != nil {
		return nil, err
	}
	return s, nil
}

// These pinned invariants prevent two equally incorrect databases from
// comparing as "equivalent". They are requirements of immutable 006.
func verifyIdentityBridge(ctx context.Context, tx pgx.Tx) error {
	var count int
	if err := tx.QueryRow(ctx, `SELECT count(*) FROM pg_class c
	 JOIN pg_namespace n ON n.oid=c.relnamespace
	 WHERE n.nspname='public' AND c.relkind='r' AND c.relname NOT IN ('schema_migrations','atlas_schema_revisions')`).Scan(&count); err != nil {
		return err
	}
	if count != 25 {
		return fmt.Errorf("expected 25 application tables after 006, got %d", count)
	}
	uniqueNames := map[string]string{
		"principals":                    "principals_account_id_plane_key",
		"auth_rate_limits":              "auth_rate_limits_bucket_key_digest_key",
		"consumed_refresh_tokens":       "consumed_refresh_tokens_digest_key",
		"auth_action_tokens":            "auth_action_tokens_digest_key",
		"admin_role_assignments":        "admin_role_assignments_account_id_role_key_key",
		"admin_capability_overrides":    "admin_capability_overrides_account_id_capability_key",
		"mutation_idempotency_receipts": "mutation_idempotency_receipts_actor_id_request_key_key",
	}
	for table, constraint := range uniqueNames {
		var pk, natural bool
		if err := tx.QueryRow(ctx, `SELECT
		 EXISTS(SELECT 1 FROM pg_constraint c JOIN pg_class t ON t.oid=c.conrelid
		 WHERE t.oid=to_regclass('public.'||$1) AND c.conname=$1||'_pkey' AND c.contype='p'
		 AND pg_get_constraintdef(c.oid)='PRIMARY KEY (id)'),
		 EXISTS(SELECT 1 FROM pg_constraint c JOIN pg_class t ON t.oid=c.conrelid
		 WHERE t.oid=to_regclass('public.'||$1) AND c.conname=$2 AND c.contype='u')`, table, constraint).Scan(&pk, &natural); err != nil {
			return err
		}
		if !pk || !natural {
			return fmt.Errorf("identity primary/retained natural unique constraint missing on %s", table)
		}
		var column bool
		if err := tx.QueryRow(ctx, `SELECT EXISTS(
		 SELECT 1 FROM pg_attribute a JOIN pg_class t ON t.oid=a.attrelid
		 WHERE t.oid=to_regclass('public.'||$1) AND a.attname='id'
		 AND a.atttypid='bigint'::regtype AND a.attnotnull AND NOT a.attisdropped)`, table).Scan(&column); err != nil {
			return err
		}
		if !column {
			return fmt.Errorf("identity BIGINT NOT NULL column missing on %s", table)
		}
	}
	// A single-column account edge is not equivalent to principal plane
	// membership. Four historical composite FKs must survive the PK bridge.
	for table, action := range map[string]string{
		"auth_sessions": "a", "customer_profiles": "r",
		"admin_role_assignments": "a", "admin_capability_overrides": "a",
	} {
		var present bool
		if err := tx.QueryRow(ctx, `SELECT EXISTS(
		 SELECT 1 FROM pg_constraint c WHERE c.conrelid=to_regclass('public.'||$1)
		 AND c.confrelid='public.principals'::regclass
		 AND c.conname=$1||'_account_id_plane_fkey' AND c.contype='f'
		 AND c.confdeltype::text=$2 AND c.convalidated
		 AND (SELECT array_agg(a.attname ORDER BY x.ord)
		 FROM unnest(c.conkey) WITH ORDINALITY x(attnum,ord)
		 JOIN pg_attribute a ON a.attrelid=c.conrelid AND a.attnum=x.attnum)
		 = ARRAY['account_id','plane']::name[]
		 AND (SELECT array_agg(a.attname ORDER BY x.ord)
		 FROM unnest(c.confkey) WITH ORDINALITY x(attnum,ord)
		 JOIN pg_attribute a ON a.attrelid=c.confrelid AND a.attnum=x.attnum)
		 = ARRAY['account_id','plane']::name[])`, table, action).Scan(&present); err != nil {
			return err
		}
		if !present {
			return fmt.Errorf("principal-plane composite FK missing or weakened on %s", table)
		}
	}
	return nil
}

func readHistory(ctx context.Context, tx pgx.Tx, s *snapshot) error {
	rows, err := tx.Query(ctx, "SELECT version,checksum FROM public.schema_migrations ORDER BY version")
	if err != nil {
		return fmt.Errorf("missing legacy migration history: %w", err)
	}
	defer rows.Close()
	for rows.Next() {
		var n int
		var digest string
		if err := rows.Scan(&n, &digest); err != nil {
			return err
		}
		version := strconv.Itoa(n)
		if legacyChecksums[version] != digest || digest == "" {
			return fmt.Errorf("unrecognized legacy migration %d or checksum drift", n)
		}
		s.History[version] = digest
	}
	if err := rows.Err(); err != nil {
		return err
	}
	if len(s.History) == 0 {
		return errors.New("empty legacy migration history; not a restored baseline")
	}
	for n := 1; n <= len(s.History); n++ {
		if _, ok := s.History[strconv.Itoa(n)]; !ok {
			return errors.New("legacy migration history has gaps")
		}
	}
	// Atlas's own revision ledger is excluded from application schema parity.
	// It must exist after adoption; absence is represented as zero.
	var exists bool
	if err := tx.QueryRow(ctx, "SELECT to_regclass('public.atlas_schema_revisions') IS NOT NULL").Scan(&exists); err != nil {
		return err
	}
	if exists {
		if err := tx.QueryRow(ctx, "SELECT count(*) FROM public.atlas_schema_revisions").Scan(&s.AtlasRevisions); err != nil {
			return err
		}
	}
	return nil
}

// Canonical catalog lines are generated server-side. Unlike pg_dump output,
// these omit owner and volatile sequence counters but preserve key DDL facts.
var catalogQueries = []string{
	`SELECT 'table', c.relname, c.relkind::text, c.relpersistence::text,
	 c.relrowsecurity::text, c.relforcerowsecurity::text, coalesce(pg_get_partkeydef(c.oid),'')
	 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
	 WHERE n.nspname='public' AND c.relkind IN ('r','p') AND c.relname <> 'atlas_schema_revisions'`,
	`SELECT 'column', c.relname, a.attname, format_type(a.atttypid,a.atttypmod),
	 a.attnotnull::text, a.attidentity::text, a.attgenerated::text,
	 coalesce(pg_get_expr(d.adbin,d.adrelid),''),
	 CASE WHEN a.attcollation=0 THEN '' ELSE coalesce(coll.collname,'') END
	 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
	 JOIN pg_attribute a ON a.attrelid=c.oid AND a.attnum>0 AND NOT a.attisdropped
	 LEFT JOIN pg_attrdef d ON d.adrelid=c.oid AND d.adnum=a.attnum
	 LEFT JOIN pg_collation coll ON coll.oid=a.attcollation
	 WHERE n.nspname='public' AND c.relkind IN ('r','p') AND c.relname <> 'atlas_schema_revisions'`,
	`SELECT 'constraint', c.relname, co.conname, co.contype::text,
	 pg_get_constraintdef(co.oid,true), co.condeferrable::text,
	 co.condeferred::text, co.convalidated::text
	 FROM pg_constraint co JOIN pg_class c ON c.oid=co.conrelid
	 JOIN pg_namespace n ON n.oid=c.relnamespace
	 WHERE n.nspname='public' AND c.relname <> 'atlas_schema_revisions'`,
	`SELECT 'index', t.relname, i.relname, pg_get_indexdef(i.oid),
	 x.indisunique::text, x.indisprimary::text, x.indisvalid::text
	 FROM pg_index x JOIN pg_class i ON i.oid=x.indexrelid
	 JOIN pg_class t ON t.oid=x.indrelid JOIN pg_namespace n ON n.oid=t.relnamespace
	 WHERE n.nspname='public' AND t.relname <> 'atlas_schema_revisions'`,
	`SELECT 'sequence', sequencename, data_type, start_value::text,
	 min_value::text, max_value::text, increment_by::text, cycle::text, cache_size::text
	 FROM pg_sequences WHERE schemaname='public' AND sequencename NOT LIKE 'atlas_schema_revisions%'`,
	`SELECT 'trigger', c.relname, t.tgname, pg_get_triggerdef(t.oid,true)
	 FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid
	 JOIN pg_namespace n ON n.oid=c.relnamespace
	 WHERE n.nspname='public' AND NOT t.tgisinternal AND c.relname <> 'atlas_schema_revisions'`,
	`SELECT 'function', p.proname, pg_get_function_identity_arguments(p.oid),pg_get_functiondef(p.oid)
	 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='public'`,
	`SELECT 'view', viewname, definition FROM pg_views WHERE schemaname='public'`,
	`SELECT 'policy', tablename, policyname, permissive, array_to_string(roles,','),cmd,
	 coalesce(qual,''),coalesce(with_check,'') FROM pg_policies WHERE schemaname='public'`,
	`SELECT 'enum', t.typname, e.enumlabel, e.enumsortorder::text
	 FROM pg_enum e JOIN pg_type t ON t.oid=e.enumtypid
	 JOIN pg_namespace n ON n.oid=t.typnamespace WHERE n.nspname='public'`,
	`SELECT 'extension', e.extname,e.extversion,n.nspname FROM pg_extension e
	 JOIN pg_namespace n ON n.oid=e.extnamespace`,
}

func readCatalog(ctx context.Context, tx pgx.Tx, s *snapshot) error {
	for _, query := range catalogQueries {
		rows, err := tx.Query(ctx, query)
		if err != nil {
			return err
		}
		fields := rows.FieldDescriptions()
		for rows.Next() {
			values := make([]string, len(fields))
			dest := make([]any, len(fields))
			for i := range values {
				dest[i] = &values[i]
			}
			if err = rows.Scan(dest...); err != nil {
				rows.Close()
				return err
			}
			b, e := json.Marshal(values)
			if e != nil {
				rows.Close()
				return e
			}
			s.Catalog = append(s.Catalog, string(b))
		}
		err = rows.Err()
		rows.Close()
		if err != nil {
			return err
		}
	}
	sort.Strings(s.Catalog)
	return nil
}

func readData(ctx context.Context, tx pgx.Tx, s *snapshot) error {
	rows, err := tx.Query(ctx, `SELECT c.relname FROM pg_class c
	 JOIN pg_namespace n ON n.oid=c.relnamespace
	 WHERE n.nspname='public' AND c.relkind='r'
	 AND c.relname NOT IN ('schema_migrations','atlas_schema_revisions') ORDER BY c.relname`)
	if err != nil {
		return err
	}
	var names []string
	for rows.Next() {
		var name string
		if err = rows.Scan(&name); err != nil {
			rows.Close()
			return err
		}
		names = append(names, name)
	}
	err = rows.Err()
	rows.Close()
	if err != nil {
		return err
	}
	for _, name := range names {
		query := "to_jsonb(t)"
		if bridgeTables[name] {
			query += " - 'id'"
		}
		stmt := fmt.Sprintf(`SELECT (%s)::text FROM %s t ORDER BY (%s)::text COLLATE "C"`,
			query, pgx.Identifier{"public", name}.Sanitize(), query)
		dataRows, e := tx.Query(ctx, stmt)
		if e != nil {
			return e
		}
		h := sha256.New()
		var count int64
		for dataRows.Next() {
			var row string
			if e = dataRows.Scan(&row); e != nil {
				break
			}
			digestRow(h, row)
			count++
		}
		if e == nil {
			e = dataRows.Err()
		}
		dataRows.Close()
		if e != nil {
			return fmt.Errorf("data fingerprint %s: %w", name, e)
		}
		s.Tables[name] = tableDigest{Rows: count, SHA256: hex.EncodeToString(h.Sum(nil))}
	}
	return nil
}

func digestRow(h hash.Hash, row string) {
	var length [8]byte
	binary.BigEndian.PutUint64(length[:], uint64(len(row)))
	h.Write(length[:])
	io.WriteString(h, row)
}

func readPair(left, right string) (*snapshot, *snapshot, error) {
	a, err := readSnapshot(left)
	if err != nil {
		return nil, nil, err
	}
	b, err := readSnapshot(right)
	if err != nil {
		return nil, nil, err
	}
	return a, b, nil
}

func readSnapshot(path string) (*snapshot, error) {
	f, err := os.Open(path)
	if err != nil {
		return nil, err
	}
	defer f.Close()
	var s snapshot
	decoder := json.NewDecoder(io.LimitReader(f, 64<<20))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&s); err != nil {
		return nil, err
	}
	var trailing any
	if err := decoder.Decode(&trailing); err != io.EOF {
		return nil, errors.New("trailing data or oversized snapshot")
	}
	if !disposableName.MatchString(s.Database) || s.ServerVersion/10000 != 18 || s.History == nil || s.Tables == nil || len(s.Catalog) == 0 {
		return nil, errors.New("invalid or incomplete PostgreSQL 18 rehearsal snapshot")
	}
	return &s, nil
}

func validHistory(s *snapshot) error {
	if len(s.History) != len(legacyChecksums) {
		return errors.New("expected all six migration ledger entries")
	}
	for k, v := range legacyChecksums {
		if s.History[k] != v {
			return fmt.Errorf("migration %s checksum or presence mismatch", k)
		}
	}
	return nil
}

func compareData(before, after *snapshot) error {
	if before.Database != after.Database {
		return errors.New("data snapshots must come from the SAME disposable database")
	}
	if before.ServerVersion != after.ServerVersion {
		return errors.New("PostgreSQL version changed")
	}
	if len(before.Tables) == 0 {
		return errors.New("empty application table inventory")
	}
	if len(before.History) < len(legacyChecksums) && before.Tables["accounts"].Rows == 0 {
		return errors.New("upgrade proof requires at least one pre-existing account")
	}
	for name, want := range before.Tables {
		got, ok := after.Tables[name]
		if !ok || got != want {
			return fmt.Errorf("data changed or table missing: %s", name)
		}
	}
	for version, digest := range before.History {
		if after.History[version] != digest {
			return fmt.Errorf("historical migration %s changed", version)
		}
	}
	return nil
}

func compareSchema(left, right *snapshot) error {
	if left.ServerVersion != right.ServerVersion {
		return errors.New("PostgreSQL versions differ")
	}
	if right.AtlasRevisions < 1 {
		return errors.New("right-hand database has no Atlas baseline revision; adoption/replay unverified")
	}
	if err := validHistory(left); err != nil {
		return fmt.Errorf("left: %w", err)
	}
	if err := validHistory(right); err != nil {
		return fmt.Errorf("right: %w", err)
	}
	if len(left.Catalog) != len(right.Catalog) {
		return fmt.Errorf("catalog entry count differs: %d vs %d", len(left.Catalog), len(right.Catalog))
	}
	for i := range left.Catalog {
		if left.Catalog[i] != right.Catalog[i] {
			return fmt.Errorf("catalog drift at ordered entry %d: %s versus %s", i, left.Catalog[i], right.Catalog[i])
		}
	}
	return nil
}
