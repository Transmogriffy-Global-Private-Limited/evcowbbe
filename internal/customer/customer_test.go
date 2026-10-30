package customer_test

import (
	"context"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"github.com/Transmogriffy-Global-Private-Limited/evcowbbe/internal/customer"
	"github.com/google/uuid"
)

type mockRepo struct {
	profile   *customer.CustomerProfile
	vehicles  map[uuid.UUID]*customer.CustomerVehicle
	addresses map[uuid.UUID]*customer.CustomerAddress
}

func newMockRepo(accountID uuid.UUID) *mockRepo {
	now := time.Now()
	profID := uuid.New()
	name := "Test Customer"
	phone := "+919876543210"
	return &mockRepo{
		profile: &customer.CustomerProfile{
			ID:           profID,
			AccountID:    accountID,
			Plane:        "app",
			FullName:     &name,
			PrimaryPhone: &phone,
			Status:       "ACTIVE",
			CreatedAt:    now,
			UpdatedAt:    now,
			Version:      1,
		},
		vehicles:  make(map[uuid.UUID]*customer.CustomerVehicle),
		addresses: make(map[uuid.UUID]*customer.CustomerAddress),
	}
}

func (m *mockRepo) GetProfileByAccountID(ctx context.Context, accountID uuid.UUID) (*customer.CustomerProfile, error) {
	if m.profile != nil && m.profile.AccountID == accountID {
		return m.profile, nil
	}
	return nil, customer.ErrNotFound
}

func (m *mockRepo) GetProfileByID(ctx context.Context, id uuid.UUID) (*customer.CustomerProfile, error) {
	if m.profile != nil && m.profile.ID == id {
		return m.profile, nil
	}
	return nil, customer.ErrNotFound
}

func (m *mockRepo) EnsureProfile(ctx context.Context, accountID uuid.UUID) (*customer.CustomerProfile, error) {
	if m.profile != nil && m.profile.AccountID == accountID {
		return m.profile, nil
	}
	now := time.Now()
	m.profile = &customer.CustomerProfile{
		ID:        uuid.New(),
		AccountID: accountID,
		Plane:     "app",
		Status:    "INCOMPLETE",
		CreatedAt: now,
		UpdatedAt: now,
		Version:   1,
	}
	return m.profile, nil
}

func (m *mockRepo) CreateProfile(ctx context.Context, accountID uuid.UUID, req *customer.CreateProfileRequest, actorAccountID *uuid.UUID) (*customer.CustomerProfile, error) {
	if m.profile != nil && m.profile.AccountID == accountID && m.profile.Status != "INCOMPLETE" {
		return nil, customer.ErrConflict
	}
	now := time.Now()
	status := "INCOMPLETE"
	if req.FullName != nil {
		status = "ACTIVE"
	}
	m.profile = &customer.CustomerProfile{
		ID:           uuid.New(),
		AccountID:    accountID,
		Plane:        "app",
		FullName:     req.FullName,
		PrimaryPhone: req.PrimaryPhone,
		Status:       status,
		CreatedAt:    now,
		UpdatedAt:    now,
		Version:      1,
	}
	return m.profile, nil
}

func (m *mockRepo) UpdateProfile(ctx context.Context, profileID uuid.UUID, req *customer.UpdateProfileRequest, actorAccountID *uuid.UUID) (*customer.CustomerProfile, error) {
	if m.profile == nil || m.profile.ID != profileID {
		return nil, customer.ErrNotFound
	}
	if req.FullName != nil {
		m.profile.FullName = req.FullName
		m.profile.Status = "ACTIVE"
	}
	if req.PrimaryPhone != nil {
		m.profile.PrimaryPhone = req.PrimaryPhone
	}
	m.profile.Version++
	m.profile.UpdatedAt = time.Now()
	return m.profile, nil
}

func (m *mockRepo) CreateVehicle(ctx context.Context, customerID uuid.UUID, req *customer.CreateVehicleRequest, actorAccountID *uuid.UUID) (*customer.CustomerVehicle, error) {
	id := uuid.New()
	now := time.Now()
	v := &customer.CustomerVehicle{
		ID:                 id,
		CustomerID:         customerID,
		Label:              req.Label,
		Make:               req.Make,
		Model:              req.Model,
		Variant:            req.Variant,
		RegistrationNumber: req.RegistrationNumber,
		BatteryCapacityWh:  req.BatteryCapacityWh,
		SupportsAC:         req.SupportsAC,
		SupportsDC:         req.SupportsDC,
		MaxACPowerW:        req.MaxACPowerW,
		MaxDCPowerW:        req.MaxDCPowerW,
		ConnectorTypes:     req.ConnectorTypes,
		IsActive:           true,
		CreatedAt:          now,
		UpdatedAt:          now,
		Version:            1,
	}
	m.vehicles[id] = v
	return v, nil
}

func (m *mockRepo) GetVehicleByID(ctx context.Context, customerID, vehicleID uuid.UUID) (*customer.CustomerVehicle, error) {
	v, ok := m.vehicles[vehicleID]
	if !ok || v.CustomerID != customerID {
		return nil, customer.ErrNotFound
	}
	return v, nil
}

func (m *mockRepo) ListVehicles(ctx context.Context, customerID uuid.UUID, activeOnly bool) ([]customer.CustomerVehicle, error) {
	var list []customer.CustomerVehicle
	for _, v := range m.vehicles {
		if v.CustomerID == customerID {
			if !activeOnly || v.IsActive {
				list = append(list, *v)
			}
		}
	}
	return list, nil
}

func (m *mockRepo) UpdateVehicle(ctx context.Context, customerID, vehicleID uuid.UUID, req *customer.UpdateVehicleRequest, actorAccountID *uuid.UUID) (*customer.CustomerVehicle, error) {
	v, ok := m.vehicles[vehicleID]
	if !ok || v.CustomerID != customerID {
		return nil, customer.ErrNotFound
	}
	if req.ExpectedVersion > 0 && v.Version != req.ExpectedVersion {
		return nil, customer.ErrVersionMismatch
	}
	if req.Make != nil {
		v.Make = *req.Make
	}
	if req.Model != nil {
		v.Model = *req.Model
	}
	if req.IsActive != nil {
		v.IsActive = *req.IsActive
	}
	v.Version++
	v.UpdatedAt = time.Now()
	return v, nil
}

func (m *mockRepo) DeleteVehicle(ctx context.Context, customerID, vehicleID uuid.UUID, actorAccountID *uuid.UUID) error {
	v, ok := m.vehicles[vehicleID]
	if !ok || v.CustomerID != customerID {
		return customer.ErrNotFound
	}
	v.IsActive = false
	v.Version++
	return nil
}

func (m *mockRepo) CreateAddress(ctx context.Context, customerID uuid.UUID, req *customer.CreateAddressRequest, actorAccountID *uuid.UUID) (*customer.CustomerAddress, error) {
	id := uuid.New()
	now := time.Now()
	cc := "IN"
	if req.CountryCode != nil {
		cc = *req.CountryCode
	}
	if req.IsDefault {
		for _, a := range m.addresses {
			if a.CustomerID == customerID {
				a.IsDefault = false
			}
		}
	}
	addr := &customer.CustomerAddress{
		ID:          id,
		CustomerID:  customerID,
		Label:       req.Label,
		Line1:       req.Line1,
		City:        req.City,
		State:       req.State,
		PostalCode:  req.PostalCode,
		CountryCode: cc,
		Latitude:    req.Latitude,
		Longitude:   req.Longitude,
		IsDefault:   req.IsDefault,
		IsActive:    true,
		CreatedAt:   now,
		UpdatedAt:   now,
		Version:     1,
	}
	m.addresses[id] = addr
	return addr, nil
}

func (m *mockRepo) GetAddressByID(ctx context.Context, customerID, addressID uuid.UUID) (*customer.CustomerAddress, error) {
	addr, ok := m.addresses[addressID]
	if !ok || addr.CustomerID != customerID {
		return nil, customer.ErrNotFound
	}
	return addr, nil
}

func (m *mockRepo) ListAddresses(ctx context.Context, customerID uuid.UUID, activeOnly bool) ([]customer.CustomerAddress, error) {
	var list []customer.CustomerAddress
	for _, a := range m.addresses {
		if a.CustomerID == customerID {
			if !activeOnly || a.IsActive {
				list = append(list, *a)
			}
		}
	}
	return list, nil
}

func (m *mockRepo) UpdateAddress(ctx context.Context, customerID, addressID uuid.UUID, req *customer.UpdateAddressRequest, actorAccountID *uuid.UUID) (*customer.CustomerAddress, error) {
	addr, ok := m.addresses[addressID]
	if !ok || addr.CustomerID != customerID {
		return nil, customer.ErrNotFound
	}
	if req.ExpectedVersion > 0 && addr.Version != req.ExpectedVersion {
		return nil, customer.ErrVersionMismatch
	}
	if req.Line1 != nil {
		addr.Line1 = *req.Line1
	}
	if req.IsDefault != nil && *req.IsDefault {
		for _, a := range m.addresses {
			if a.CustomerID == customerID {
				a.IsDefault = false
			}
		}
		addr.IsDefault = true
	}
	addr.Version++
	addr.UpdatedAt = time.Now()
	return addr, nil
}

func (m *mockRepo) DeleteAddress(ctx context.Context, customerID, addressID uuid.UUID, actorAccountID *uuid.UUID) error {
	addr, ok := m.addresses[addressID]
	if !ok || addr.CustomerID != customerID {
		return customer.ErrNotFound
	}
	addr.IsActive = false
	addr.IsDefault = false
	addr.Version++
	return nil
}

func (m *mockRepo) SetDefaultAddress(ctx context.Context, customerID, addressID uuid.UUID, actorAccountID *uuid.UUID) error {
	addr, ok := m.addresses[addressID]
	if !ok || addr.CustomerID != customerID {
		return customer.ErrNotFound
	}
	for _, a := range m.addresses {
		if a.CustomerID == customerID {
			a.IsDefault = false
		}
	}
	addr.IsDefault = true
	addr.Version++
	return nil
}

func TestValidation(t *testing.T) {
	// Vehicle validation
	invalidVeh := &customer.CreateVehicleRequest{Make: "", Model: "Model 3"}
	if err := invalidVeh.Validate(); err == nil {
		t.Fatal("expected error on empty make")
	}

	validVeh := &customer.CreateVehicleRequest{
		Make:  "Tata",
		Model: "Nexon EV",
	}
	if err := validVeh.Validate(); err != nil {
		t.Fatalf("unexpected validation error: %v", err)
	}

	// Address validation
	invalidAddr := &customer.CreateAddressRequest{
		Line1: "123 Main St",
		City:  "Kolkata",
		State: "West Bengal",
		Latitude: stringPtr("22.5726"),
	}
	if err := invalidAddr.Validate(); err == nil {
		t.Fatal("expected error when latitude provided without longitude")
	}

	validAddr := &customer.CreateAddressRequest{
		Line1:     "123 Main St",
		City:      "Kolkata",
		State:     "West Bengal",
		Latitude:  stringPtr("22.5726"),
		Longitude: stringPtr("88.3639"),
	}
	if err := validAddr.Validate(); err != nil {
		t.Fatalf("unexpected validation error: %v", err)
	}
}

func TestCustomerServiceCRUD(t *testing.T) {
	ctx := context.Background()
	accountID := uuid.New()
	repo := newMockRepo(accountID)
	svc := customer.NewService(repo)

	// 1. Profile: GET and PATCH/Update
	prof, err := svc.GetProfile(ctx, accountID)
	if err != nil {
		t.Fatalf("failed to get profile: %v", err)
	}
	if prof.AccountID != accountID {
		t.Fatalf("account ID mismatch: got %v, want %v", prof.AccountID, accountID)
	}

	updatedName := "New Name"
	updatedProf, err := svc.UpdateProfile(ctx, accountID, &customer.UpdateProfileRequest{
		FullName: &updatedName,
	})
	if err != nil {
		t.Fatalf("failed to update profile: %v", err)
	}
	if updatedProf.FullName == nil || *updatedProf.FullName != updatedName {
		t.Fatalf("name mismatch: got %v, want %s", updatedProf.FullName, updatedName)
	}

	// Create new account and POST profile
	accountID2 := uuid.New()
	createdProf, err := svc.CreateProfile(ctx, accountID2, &customer.CreateProfileRequest{
		FullName: stringPtr("Second User"),
	})
	if err != nil {
		t.Fatalf("failed to create profile: %v", err)
	}
	if createdProf.FullName == nil || *createdProf.FullName != "Second User" {
		t.Fatalf("created name mismatch: %v", createdProf.FullName)
	}

	// 2. Vehicle CRUD: POST, GET, PATCH
	veh, err := svc.CreateVehicle(ctx, accountID, &customer.CreateVehicleRequest{
		Make:           "Tata",
		Model:          "Nexon EV",
		ConnectorTypes: []string{"CCS2", "Type2"},
	})
	if err != nil {
		t.Fatalf("failed to create vehicle: %v", err)
	}
	if veh.Make != "Tata" {
		t.Fatalf("vehicle make mismatch: %s", veh.Make)
	}

	list, err := svc.ListVehicles(ctx, accountID, true)
	if err != nil || len(list) != 1 {
		t.Fatalf("list vehicles failed: len=%d, err=%v", len(list), err)
	}

	gotVeh, err := svc.GetVehicle(ctx, accountID, veh.ID)
	if err != nil || gotVeh.ID != veh.ID {
		t.Fatalf("get vehicle failed: %v", err)
	}

	newMake := "Tata Motors"
	updatedVeh, err := svc.UpdateVehicle(ctx, accountID, veh.ID, &customer.UpdateVehicleRequest{
		Make:            &newMake,
		ExpectedVersion: veh.Version,
	})
	if err != nil || updatedVeh.Make != newMake {
		t.Fatalf("update vehicle failed: %v", err)
	}

	if err := svc.DeleteVehicle(ctx, accountID, veh.ID); err != nil {
		t.Fatalf("delete vehicle failed: %v", err)
	}

	// 3. Address CRUD: POST, GET, PATCH
	addr1, err := svc.CreateAddress(ctx, accountID, &customer.CreateAddressRequest{
		Line1:     "123 Park Street",
		City:      "Kolkata",
		State:     "West Bengal",
		IsDefault: true,
	})
	if err != nil {
		t.Fatalf("failed to create address 1: %v", err)
	}
	if !addr1.IsDefault {
		t.Fatal("address 1 should be default")
	}

	addr2, err := svc.CreateAddress(ctx, accountID, &customer.CreateAddressRequest{
		Line1:     "456 Salt Lake",
		City:      "Kolkata",
		State:     "West Bengal",
		IsDefault: true,
	})
	if err != nil {
		t.Fatalf("failed to create address 2: %v", err)
	}
	if !addr2.IsDefault {
		t.Fatal("address 2 should be default")
	}

	refreshedAddr1, _ := svc.GetAddress(ctx, accountID, addr1.ID)
	if refreshedAddr1.IsDefault {
		t.Fatal("addr1 should no longer be default after addr2 set to default")
	}

	// PATCH address
	updatedLine1 := "123 Park Street, Suite 5"
	patchedAddr, err := svc.UpdateAddress(ctx, accountID, addr1.ID, &customer.UpdateAddressRequest{
		Line1: &updatedLine1,
	})
	if err != nil || patchedAddr.Line1 != updatedLine1 {
		t.Fatalf("patch address failed: %v", err)
	}

	if err := svc.SetDefaultAddress(ctx, accountID, addr1.ID); err != nil {
		t.Fatalf("failed to set default address: %v", err)
	}
	refreshedAddr1, _ = svc.GetAddress(ctx, accountID, addr1.ID)
	if !refreshedAddr1.IsDefault {
		t.Fatal("addr1 should be default again")
	}
}

func stringPtr(s string) *string {
	return &s
}

func TestRouterUnauthorized(t *testing.T) {
	accountID := uuid.New()
	repo := newMockRepo(accountID)
	svc := customer.NewService(repo)
	router := customer.NewRouter(svc, nil)

	req := httptest.NewRequest("GET", "/api/v1/app/customer/profile", nil)
	w := httptest.NewRecorder()
	router.ServeHTTP(w, req)

	if w.Code != http.StatusUnauthorized {
		t.Fatalf("expected 401 Unauthorized without bearer token, got %d", w.Code)
	}
}
