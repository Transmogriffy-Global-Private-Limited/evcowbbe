package ent_test

import (
	"context"
	"testing"
	"time"

	"entgo.io/ent/dialect"
	entsql "entgo.io/ent/dialect/sql"
	"github.com/DATA-DOG/go-sqlmock"
	appent "github.com/Transmogriffy-Global-Private-Limited/evcowbbe/ent"
	"github.com/Transmogriffy-Global-Private-Limited/evcowbbe/ent/customeraddress"
	"github.com/Transmogriffy-Global-Private-Limited/evcowbbe/ent/customervehicle"
	"github.com/google/uuid"
)

// TestGeneratedCustomerQueries checks the generated, typed Ent query surface
// and PostgreSQL text[] scanning without requiring an external database.
// This complements, but does not replace, an actual PostgreSQL integration test.
func TestGeneratedCustomerQueries(t *testing.T) {
	db, mock, err := sqlmock.New()
	if err != nil {
		t.Fatal(err)
	}
	client := appent.NewClient(appent.Driver(entsql.OpenDB(dialect.Postgres, db)))
	defer client.Close()

	id := uuid.New()
	owner := uuid.New()
	now := time.Now().UTC().Truncate(time.Microsecond)
	rows := sqlmock.NewRows(customervehicle.Columns).AddRow(
		id, owner, "Daily", "Tata", "Nexon", nil, "WB01AA1000",
		int64(40000), true, true, int32(7000), int32(60000),
		`{CCS2,Type2}`, true, now, now, int64(2),
	)
	mock.ExpectQuery(`SELECT .* FROM "customer_vehicles"`).WithArgs(id).WillReturnRows(rows)
	vehicle, err := client.CustomerVehicle.Query().Where(customervehicle.IDEQ(id)).Only(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	if vehicle.CustomerID != owner || vehicle.Model != "Nexon" || vehicle.ConnectorTypes == nil ||
		len(*vehicle.ConnectorTypes) != 2 || (*vehicle.ConnectorTypes)[1] != "Type2" {
		t.Fatalf("generated model mapping mismatch: %+v", vehicle)
	}
	if err := mock.ExpectationsWereMet(); err != nil {
		t.Fatal(err)
	}
}

func TestGeneratedExactDecimalCoordinates(t *testing.T) {
	db, mock, err := sqlmock.New()
	if err != nil {
		t.Fatal(err)
	}
	client := appent.NewClient(appent.Driver(entsql.OpenDB(dialect.Postgres, db)))
	defer client.Close()

	id := uuid.New()
	owner := uuid.New()
	now := time.Now().UTC().Truncate(time.Microsecond)
	rows := sqlmock.NewRows(customeraddress.Columns).AddRow(
		id, owner, nil, "1 Some Street", nil, nil, "Kolkata", "West Bengal", "700001",
		"IN", "22.947557", "88.461494", nil, nil, nil, nil, nil, nil,
		true, true, now, now, int64(1),
	)
	mock.ExpectQuery(`SELECT .* FROM "customer_addresses"`).WithArgs(id).WillReturnRows(rows)
	address, err := client.CustomerAddress.Query().Where(customeraddress.IDEQ(id)).Only(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	if address.Latitude == nil || address.Longitude == nil ||
		address.Latitude.String() != "22.947557" || address.Longitude.String() != "88.461494" {
		t.Fatalf("decimal coordinate mapping mismatch: %+v", address)
	}
	if err := mock.ExpectationsWereMet(); err != nil {
		t.Fatal(err)
	}
}
