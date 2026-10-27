package user

import (
	"context"
	"strings"
)

// Service provides customer vehicle business operations.
type Service struct {
	repo Repository
}

// NewService creates a new Service instance backed by the given repository.
func NewService(repo Repository) *Service {
	return &Service{repo: repo}
}

// Create validates and registers a new vehicle for the customer.
func (s *Service) Create(ctx context.Context, accountID string, req CreateVehicleRequest) (*VehicleResponse, error) {
	if strings.TrimSpace(accountID) == "" {
		return nil, ErrUnauthorized
	}

	if err := req.Validate(); err != nil {
		return nil, err
	}

	return s.repo.Create(ctx, accountID, req)
}

// Get retrieves a customer vehicle by ID, ensuring ownership.
func (s *Service) Get(ctx context.Context, accountID, vehicleID string) (*VehicleResponse, error) {
	if strings.TrimSpace(accountID) == "" {
		return nil, ErrUnauthorized
	}
	if strings.TrimSpace(vehicleID) == "" {
		return nil, ErrNotFound
	}

	return s.repo.GetByID(ctx, accountID, vehicleID)
}

// List returns all registered vehicles belonging to the customer.
func (s *Service) List(ctx context.Context, accountID string) ([]VehicleResponse, error) {
	if strings.TrimSpace(accountID) == "" {
		return nil, ErrUnauthorized
	}

	return s.repo.ListByAccount(ctx, accountID)
}

// Update validates and updates fields of an existing customer vehicle.
func (s *Service) Update(ctx context.Context, accountID, vehicleID string, req UpdateVehicleRequest) (*VehicleResponse, error) {
	if strings.TrimSpace(accountID) == "" {
		return nil, ErrUnauthorized
	}
	if strings.TrimSpace(vehicleID) == "" {
		return nil, ErrNotFound
	}

	if err := req.Validate(); err != nil {
		return nil, err
	}

	return s.repo.Update(ctx, accountID, vehicleID, req)
}

// Delete removes a customer vehicle by ID.
func (s *Service) Delete(ctx context.Context, accountID, vehicleID string) error {
	if strings.TrimSpace(accountID) == "" {
		return ErrUnauthorized
	}
	if strings.TrimSpace(vehicleID) == "" {
		return ErrNotFound
	}

	return s.repo.Delete(ctx, accountID, vehicleID)
}

// SetDefault marks the specified vehicle as the customer's primary vehicle.
func (s *Service) SetDefault(ctx context.Context, accountID, vehicleID string) error {
	if strings.TrimSpace(accountID) == "" {
		return ErrUnauthorized
	}
	if strings.TrimSpace(vehicleID) == "" {
		return ErrNotFound
	}

	return s.repo.SetDefault(ctx, accountID, vehicleID)
}

