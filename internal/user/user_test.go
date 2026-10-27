package user

import (
	"bytes"
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"github.com/Transmogriffy-Global-Private-Limited/evcowbbe/internal/auth"
)

type mockRepository struct {
	vehicles map[string]VehicleResponse
}

func newMockRepository() *mockRepository {
	return &mockRepository{vehicles: make(map[string]VehicleResponse)}
}

func (m *mockRepository) Create(ctx context.Context, accountID string, req CreateVehicleRequest) (*VehicleResponse, error) {
	for _, v := range m.vehicles {
		if v.CustomerAccountID == accountID && v.RegistrationNumber == req.RegistrationNumber {
			return nil, ErrConflict
		}
	}
	id := "mock-veh-" + req.RegistrationNumber
	res := VehicleResponse{
		ID:                 id,
		CustomerAccountID:  accountID,
		Name:               req.Name,
		VehicleType:        req.VehicleType,
		Make:               req.Make,
		Model:              req.Model,
		RegistrationNumber: req.RegistrationNumber,
		BatteryCapacityKWh: req.BatteryCapacityKWh,
		IsDefault:          req.IsDefault || len(m.vehicles) == 0,
		CreatedAt:          time.Now().UTC(),
		UpdatedAt:          time.Now().UTC(),
	}
	if res.IsDefault {
		for k, v := range m.vehicles {
			if v.CustomerAccountID == accountID {
				v.IsDefault = false
				m.vehicles[k] = v
			}
		}
	}
	m.vehicles[id] = res
	return &res, nil
}

func (m *mockRepository) GetByID(ctx context.Context, accountID, vehicleID string) (*VehicleResponse, error) {
	v, exists := m.vehicles[vehicleID]
	if !exists || v.CustomerAccountID != accountID {
		return nil, ErrNotFound
	}
	return &v, nil
}

func (m *mockRepository) ListByAccount(ctx context.Context, accountID string) ([]VehicleResponse, error) {
	var list []VehicleResponse
	for _, v := range m.vehicles {
		if v.CustomerAccountID == accountID {
			list = append(list, v)
		}
	}
	return list, nil
}

func (m *mockRepository) Update(ctx context.Context, accountID, vehicleID string, req UpdateVehicleRequest) (*VehicleResponse, error) {
	v, exists := m.vehicles[vehicleID]
	if !exists || v.CustomerAccountID != accountID {
		return nil, ErrNotFound
	}
	if req.Name != nil {
		v.Name = req.Name
	}
	if req.VehicleType != nil {
		v.VehicleType = *req.VehicleType
	}
	if req.Make != nil {
		v.Make = req.Make
	}
	if req.Model != nil {
		v.Model = req.Model
	}
	if req.RegistrationNumber != nil {
		v.RegistrationNumber = *req.RegistrationNumber
	}
	if req.BatteryCapacityKWh != nil {
		v.BatteryCapacityKWh = req.BatteryCapacityKWh
	}
	if req.IsDefault != nil && *req.IsDefault {
		for k, item := range m.vehicles {
			if item.CustomerAccountID == accountID {
				item.IsDefault = false
				m.vehicles[k] = item
			}
		}
		v.IsDefault = true
	}
	v.UpdatedAt = time.Now().UTC()
	m.vehicles[vehicleID] = v
	return &v, nil
}

func (m *mockRepository) Delete(ctx context.Context, accountID, vehicleID string) error {
	v, exists := m.vehicles[vehicleID]
	if !exists || v.CustomerAccountID != accountID {
		return ErrNotFound
	}
	delete(m.vehicles, vehicleID)
	return nil
}

func (m *mockRepository) SetDefault(ctx context.Context, accountID, vehicleID string) error {
	v, exists := m.vehicles[vehicleID]
	if !exists || v.CustomerAccountID != accountID {
		return ErrNotFound
	}
	for k, item := range m.vehicles {
		if item.CustomerAccountID == accountID {
			item.IsDefault = false
			m.vehicles[k] = item
		}
	}
	v.IsDefault = true
	m.vehicles[vehicleID] = v
	return nil
}

type mockAuthenticator struct {
	validToken string
	accountID  string
	plane      string
}

func (m *mockAuthenticator) Authenticate(ctx context.Context, bearer, plane string) (auth.Principal, error) {
	if bearer != m.validToken {
		return auth.Principal{}, auth.ErrInvalid
	}
	if plane != m.plane {
		return auth.Principal{}, auth.ErrForbidden
	}
	return auth.Principal{AccountID: m.accountID, Plane: m.plane, SessionID: "mock-session"}, nil
}

func TestValidation(t *testing.T) {
	t.Run("CreateVehicleRequest Valid", func(t *testing.T) {
		name := "My Nexon"
		kwh := 40.5
		req := CreateVehicleRequest{
			Name:               &name,
			VehicleType:        "CAR",
			RegistrationNumber: "KA01AB1234",
			BatteryCapacityKWh: &kwh,
		}
		if err := req.Validate(); err != nil {
			t.Fatalf("unexpected validation error: %v", err)
		}
		if req.RegistrationNumber != "KA01AB1234" {
			t.Errorf("registration number not normalized: %s", req.RegistrationNumber)
		}
	})

	t.Run("CreateVehicleRequest Missing Registration", func(t *testing.T) {
		req := CreateVehicleRequest{
			VehicleType:        "CAR",
			RegistrationNumber: " ",
		}
		if err := req.Validate(); err == nil {
			t.Fatal("expected error for empty registration number")
		}
	})

	t.Run("CreateVehicleRequest Invalid VehicleType", func(t *testing.T) {
		req := CreateVehicleRequest{
			VehicleType:        "AIRPLANE",
			RegistrationNumber: "KA01AB1234",
		}
		if err := req.Validate(); err == nil {
			t.Fatal("expected error for invalid vehicle type")
		}
	})

	t.Run("CreateVehicleRequest Negative Battery", func(t *testing.T) {
		kwh := -10.0
		req := CreateVehicleRequest{
			VehicleType:        "CAR",
			RegistrationNumber: "KA01AB1234",
			BatteryCapacityKWh: &kwh,
		}
		if err := req.Validate(); err == nil {
			t.Fatal("expected error for negative battery capacity")
		}
	})

	t.Run("UpdateVehicleRequest Empty", func(t *testing.T) {
		req := UpdateVehicleRequest{}
		if err := req.Validate(); err == nil {
			t.Fatal("expected error for empty update request")
		}
	})
}

func TestRouterEndpoints(t *testing.T) {
	repo := newMockRepository()
	svc := NewService(repo)
	authMock := &mockAuthenticator{
		validToken: "valid-customer-access-token-1234567890",
		accountID:  "customer-account-1",
		plane:      "app",
	}
	router := NewRouter(svc, authMock)

	var createdVehicleID string

	t.Run("POST /api/v1/app/vehicles Unauthorized", func(t *testing.T) {
		body := `{"vehicle_type":"CAR","registration_number":"DL01AB9999"}`
		req := httptest.NewRequest(http.MethodPost, "/api/v1/app/vehicles", bytes.NewBufferString(body))
		req.Header.Set("Content-Type", "application/json")
		req.Header.Set("Authorization", "Bearer invalid-token")
		rec := httptest.NewRecorder()

		router.ServeHTTP(rec, req)
		if rec.Code != http.StatusUnauthorized {
			t.Fatalf("expected 401, got %d: %s", rec.Code, rec.Body.String())
		}
	})

	t.Run("POST /api/v1/app/vehicles Success", func(t *testing.T) {
		body := `{"vehicle_type":"CAR","make":"Tata","model":"Nexon EV","registration_number":"KA01AB1234","battery_capacity_kwh":40.5,"is_default":true}`
		req := httptest.NewRequest(http.MethodPost, "/api/v1/app/vehicles", bytes.NewBufferString(body))
		req.Header.Set("Content-Type", "application/json")
		req.Header.Set("Authorization", "Bearer "+authMock.validToken)
		rec := httptest.NewRecorder()

		router.ServeHTTP(rec, req)
		if rec.Code != http.StatusCreated {
			t.Fatalf("expected 201, got %d: %s", rec.Code, rec.Body.String())
		}

		var resp struct {
			Data VehicleResponse `json:"data"`
		}
		if err := json.NewDecoder(rec.Body).Decode(&resp); err != nil {
			t.Fatal(err)
		}
		if resp.Data.RegistrationNumber != "KA01AB1234" {
			t.Errorf("wrong reg number: %s", resp.Data.RegistrationNumber)
		}
		createdVehicleID = resp.Data.ID
	})

	t.Run("POST /api/v1/app/vehicles Conflict", func(t *testing.T) {
		body := `{"vehicle_type":"CAR","registration_number":"KA01AB1234"}`
		req := httptest.NewRequest(http.MethodPost, "/api/v1/app/vehicles", bytes.NewBufferString(body))
		req.Header.Set("Content-Type", "application/json")
		req.Header.Set("Authorization", "Bearer "+authMock.validToken)
		rec := httptest.NewRecorder()

		router.ServeHTTP(rec, req)
		if rec.Code != http.StatusConflict {
			t.Fatalf("expected 409, got %d: %s", rec.Code, rec.Body.String())
		}
	})

	t.Run("GET /api/v1/app/vehicles List", func(t *testing.T) {
		req := httptest.NewRequest(http.MethodGet, "/api/v1/app/vehicles", nil)
		req.Header.Set("Authorization", "Bearer "+authMock.validToken)
		rec := httptest.NewRecorder()

		router.ServeHTTP(rec, req)
		if rec.Code != http.StatusOK {
			t.Fatalf("expected 200, got %d: %s", rec.Code, rec.Body.String())
		}

		var resp struct {
			Data []VehicleResponse `json:"data"`
		}
		if err := json.NewDecoder(rec.Body).Decode(&resp); err != nil {
			t.Fatal(err)
		}
		if len(resp.Data) != 1 {
			t.Fatalf("expected 1 vehicle, got %d", len(resp.Data))
		}
	})

	t.Run("GET /api/v1/app/vehicles/{id} Success", func(t *testing.T) {
		req := httptest.NewRequest(http.MethodGet, "/api/v1/app/vehicles/"+createdVehicleID, nil)
		req.Header.Set("Authorization", "Bearer "+authMock.validToken)
		rec := httptest.NewRecorder()

		router.ServeHTTP(rec, req)
		if rec.Code != http.StatusOK {
			t.Fatalf("expected 200, got %d: %s", rec.Code, rec.Body.String())
		}
	})

	t.Run("PUT /api/v1/app/vehicles/{id} Update", func(t *testing.T) {
		body := `{"name":"Daily Commuter"}`
		req := httptest.NewRequest(http.MethodPut, "/api/v1/app/vehicles/"+createdVehicleID, bytes.NewBufferString(body))
		req.Header.Set("Content-Type", "application/json")
		req.Header.Set("Authorization", "Bearer "+authMock.validToken)
		rec := httptest.NewRecorder()

		router.ServeHTTP(rec, req)
		if rec.Code != http.StatusOK {
			t.Fatalf("expected 200, got %d: %s", rec.Code, rec.Body.String())
		}

		var resp struct {
			Data VehicleResponse `json:"data"`
		}
		if err := json.NewDecoder(rec.Body).Decode(&resp); err != nil {
			t.Fatal(err)
		}
		if resp.Data.Name == nil || *resp.Data.Name != "Daily Commuter" {
			t.Fatalf("name not updated")
		}
	})

	t.Run("POST /api/v1/app/vehicles/{id}/default SetDefault", func(t *testing.T) {
		req := httptest.NewRequest(http.MethodPost, "/api/v1/app/vehicles/"+createdVehicleID+"/default", nil)
		req.Header.Set("Authorization", "Bearer "+authMock.validToken)
		rec := httptest.NewRecorder()

		router.ServeHTTP(rec, req)
		if rec.Code != http.StatusOK {
			t.Fatalf("expected 200, got %d: %s", rec.Code, rec.Body.String())
		}
	})

	t.Run("DELETE /api/v1/app/vehicles/{id} Delete", func(t *testing.T) {
		req := httptest.NewRequest(http.MethodDelete, "/api/v1/app/vehicles/"+createdVehicleID, nil)
		req.Header.Set("Authorization", "Bearer "+authMock.validToken)
		rec := httptest.NewRecorder()

		router.ServeHTTP(rec, req)
		if rec.Code != http.StatusOK {
			t.Fatalf("expected 200, got %d: %s", rec.Code, rec.Body.String())
		}

		// Verify deletion
		reqGet := httptest.NewRequest(http.MethodGet, "/api/v1/app/vehicles/"+createdVehicleID, nil)
		reqGet.Header.Set("Authorization", "Bearer "+authMock.validToken)
		recGet := httptest.NewRecorder()
		router.ServeHTTP(recGet, reqGet)
		if recGet.Code != http.StatusNotFound {
			t.Fatalf("expected 404 after delete, got %d", recGet.Code)
		}
	})
}
