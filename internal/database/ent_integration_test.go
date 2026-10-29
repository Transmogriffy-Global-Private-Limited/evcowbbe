package database

import (
	"context"
	"fmt"
	"net/url"
	"os"
	"strings"
	"testing"
	"time"

	"github.com/Transmogriffy-Global-Private-Limited/evcowbbe/ent/customermasteraudit"
	"github.com/Transmogriffy-Global-Private-Limited/evcowbbe/ent/customerprofile"
	"github.com/google/uuid"
	"github.com/lib/pq"
	"github.com/shopspring/decimal"
)

// TestIntegrationEntSharedPoolCRUD exercises the production Ent adapter against
// real PostgreSQL. It never creates schemas and refuses non-disposable URLs.
// All domain writes occur in a rolled-back Ent transaction. The temporary
// authentication principal is removed at the end.
func TestIntegrationEntSharedPoolCRUD(t *testing.T) {
	uri := os.Getenv("TEST_DATABASE_URL")
	if uri == "" {
		t.Skip("TEST_DATABASE_URL not configured; PostgreSQL Ent integration not run")
	}
	u, err := url.Parse(uri)
	if err != nil || (u.Scheme != "postgres" && u.Scheme != "postgresql") ||
		u.Hostname() == "" || strings.Count(u.Path, "/") != 1 ||
		!(strings.HasPrefix(strings.TrimPrefix(u.Path, "/"), "evcowbbe_f3_") ||
			strings.HasPrefix(strings.TrimPrefix(u.Path, "/"), "evcowbbe_atlas_")) ||
		!strings.HasSuffix(u.Path, "_test") || uri == os.Getenv("DATABASE_URL") {
		t.Fatal("refusing non-disposable Ent integration database")
	}

	ctx, cancel := context.WithTimeout(context.Background(), 60*time.Second)
	defer cancel()
	pool, err := Open(ctx, uri, 4)
	if err != nil {
		t.Fatal(err)
	}
	defer pool.Close()
	if err := VerifySchema(ctx, pool); err != nil {
		t.Fatalf("expected migrations 001-005 before Ent integration: %v", err)
	}
	client, err := OpenEntClient(pool)
	if err != nil {
		t.Fatal(err)
	}
	defer client.Close()

	accountID := uuid.New()
	email := fmt.Sprintf("ent-%s@example.test", strings.ReplaceAll(accountID.String(), "-", ""))
	defer func() {
		cleanCtx, done := context.WithTimeout(context.Background(), 10*time.Second)
		defer done()
		for _, stmt := range []string{
			`DELETE FROM principals WHERE account_id=$1`,
			`DELETE FROM accounts WHERE id=$1`,
		} {
			if _, e := pool.Exec(cleanCtx, stmt, accountID); e != nil {
				t.Errorf("Ent fixture cleanup failed: %v", e)
			}
		}
	}()
	if _, err := pool.Exec(ctx,
		`INSERT INTO accounts(id,email,password_hash,email_verified_at) VALUES($1,$2,'ent-integration-test-only',now())`,
		accountID, email); err != nil {
		t.Fatal(err)
	}
	if _, err := pool.Exec(ctx,
		`INSERT INTO principals(account_id,plane,status) VALUES($1,'app','ACTIVE')`,
		accountID); err != nil {
		t.Fatal(err)
	}

	// Use the actual Ent transaction backed by the shared pgxpool adapter.
	tx, err := client.Tx(ctx)
	if err != nil {
		t.Fatal(err)
	}
	defer func() {
		if err := tx.Rollback(); err != nil {
			t.Errorf("Ent transaction rollback failed: %v", err)
		}
	}()

	profile, err := tx.CustomerProfile.Create().
		SetAccountID(accountID).
		SetFullName("Ent Integration Test").
		SetStatus(customerprofile.StatusACTIVE).
		Save(ctx)
	if err != nil {
		t.Fatal(err)
	}
	connectors := pq.StringArray{"CCS2", "Type2"}
	vehicle, err := tx.CustomerVehicle.Create().
		SetCustomerID(profile.ID).
		SetMake("Tata").
		SetModel("Nexon").
		SetConnectorTypes(&connectors).
		Save(ctx)
	if err != nil {
		t.Fatal(err)
	}
	latitude := decimal.RequireFromString("22.947557")
	longitude := decimal.RequireFromString("88.461494")
	address, err := tx.CustomerAddress.Create().
		SetCustomerID(profile.ID).
		SetLine1("Integration test address").
		SetCity("Kolkata").
		SetState("West Bengal").
		SetLatitude(&latitude).
		SetLongitude(&longitude).
		Save(ctx)
	if err != nil {
		t.Fatal(err)
	}
	changed := pq.StringArray{"full_name"}
	if _, err := tx.CustomerMasterAudit.Create().
		SetCustomerID(profile.ID).
		SetActorAccountID(accountID).
		SetResourceKind(customermasteraudit.ResourceKindPROFILE).
		SetResourceID(profile.ID).
		SetAction("CREATED").
		SetResultingVersion(1).
		SetChangedFields(&changed).
		Save(ctx); err != nil {
		t.Fatal(err)
	}

	got, err := tx.CustomerProfile.Query().
		Where(customerprofile.IDEQ(profile.ID)).
		WithVehicles().WithAddresses().Only(ctx)
	if err != nil {
		t.Fatal(err)
	}
	if len(got.Edges.Vehicles) != 1 || got.Edges.Vehicles[0].ID != vehicle.ID ||
		len(got.Edges.Addresses) != 1 || got.Edges.Addresses[0].ID != address.ID {
		t.Fatalf("Ent relationship roundtrip mismatch: %+v", got.Edges)
	}
	if got.Edges.Vehicles[0].ConnectorTypes == nil ||
		len(*got.Edges.Vehicles[0].ConnectorTypes) != 2 ||
		(*got.Edges.Vehicles[0].ConnectorTypes)[1] != "Type2" {
		t.Fatal("PostgreSQL text[] roundtrip mismatch")
	}
	if got.Edges.Addresses[0].Latitude == nil ||
		got.Edges.Addresses[0].Latitude.String() != latitude.String() ||
		got.Edges.Addresses[0].Longitude == nil ||
		got.Edges.Addresses[0].Longitude.String() != longitude.String() {
		t.Fatal("PostgreSQL numeric(9,6) roundtrip mismatch")
	}
	updated, err := tx.CustomerProfile.UpdateOneID(profile.ID).
		SetFullName("Ent Updated Name").AddVersion(1).Save(ctx)
	if err != nil {
		t.Fatal(err)
	}
	if updated.Version != 2 || updated.FullName == nil || *updated.FullName != "Ent Updated Name" {
		t.Fatalf("Ent update roundtrip mismatch: %+v", updated)
	}
	if _, err := tx.PricingPolicy.Query().Count(ctx); err != nil {
		t.Fatalf("Ent pricing policy query failed: %v", err)
	}
	if _, err := tx.PricingPolicyAudit.Query().Count(ctx); err != nil {
		t.Fatalf("Ent pricing audit query failed: %v", err)
	}
}
