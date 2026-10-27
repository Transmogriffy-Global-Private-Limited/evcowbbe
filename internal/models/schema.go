// Package models contains row/transport representations for Charger on Wheels.
// PostgreSQL constraints and relationships are defined by internal/database/migrations.
// This project uses pgx; these are deliberately plain Go structs, not GORM models.
package models

import (
	"encoding/json"
	"time"
)

type DriverVerificationStatus string

const (
	DriverVerificationDraft    DriverVerificationStatus = "DRAFT"
	DriverVerificationPending  DriverVerificationStatus = "PENDING"
	DriverVerificationVerified DriverVerificationStatus = "VERIFIED"
	DriverVerificationRejected DriverVerificationStatus = "REJECTED"
)

type DriverLifecycleStatus string

const (
	DriverLifecycleActive  DriverLifecycleStatus = "ACTIVE"
	DriverLifecycleBanned  DriverLifecycleStatus = "BANNED"
	DriverLifecycleDeleted DriverLifecycleStatus = "DELETED"
)

type DriverAvailabilityStatus string

const (
	DriverOffline   DriverAvailabilityStatus = "OFFLINE"
	DriverAvailable DriverAvailabilityStatus = "AVAILABLE"
	DriverOnJob     DriverAvailabilityStatus = "ON_JOB"
)

type DriverDocumentType string

const (
	DocumentAadhaar            DriverDocumentType = "AADHAAR"
	DocumentPAN                DriverDocumentType = "PAN"
	DocumentDrivingLicense     DriverDocumentType = "DRIVING_LICENSE"
	DocumentAddressProof       DriverDocumentType = "ADDRESS_PROOF"
	DocumentPoliceVerification DriverDocumentType = "POLICE_VERIFICATION"
	DocumentBankProof          DriverDocumentType = "BANK_PROOF"
)

type DocumentStatus string

const (
	DocumentPending  DocumentStatus = "PENDING"
	DocumentApproved DocumentStatus = "APPROVED"
	DocumentRejected DocumentStatus = "REJECTED"
	DocumentExpired  DocumentStatus = "EXPIRED"
)

type CustomerVehicleType string

const (
	VehicleCar          CustomerVehicleType = "CAR"
	VehicleMotorcycle   CustomerVehicleType = "MOTORCYCLE"
	VehicleScooter      CustomerVehicleType = "SCOOTER"
	VehicleThreeWheeler CustomerVehicleType = "THREE_WHEELER"
	VehicleOther        CustomerVehicleType = "OTHER"
)

type ChargerType string

const (
	ChargerPortableAC ChargerType = "PORTABLE_AC"
	ChargerPortableDC ChargerType = "PORTABLE_DC"
	ChargerFastDC     ChargerType = "FAST_DC"
	ChargerOther      ChargerType = "OTHER"
)

type ServiceVehicleStatus string

const (
	ServiceVehicleAvailable   ServiceVehicleStatus = "AVAILABLE"
	ServiceVehicleInService   ServiceVehicleStatus = "IN_SERVICE"
	ServiceVehicleMaintenance ServiceVehicleStatus = "MAINTENANCE"
	ServiceVehicleRetired     ServiceVehicleStatus = "RETIRED"
)

type BookingStatus string

const (
	BookingBooked        BookingStatus = "BOOKED"
	BookingAssigned      BookingStatus = "ASSIGNED"
	BookingDriverEnRoute BookingStatus = "DRIVER_EN_ROUTE"
	BookingArrived       BookingStatus = "ARRIVED"
	BookingCharging      BookingStatus = "CHARGING"
	BookingCompleted     BookingStatus = "COMPLETED"
	BookingCancelled     BookingStatus = "CANCELLED"
	BookingFailed        BookingStatus = "FAILED"
)

type BookingPaymentStatus string

const (
	BookingPaid              BookingPaymentStatus = "PAID"
	BookingPartiallyRefunded BookingPaymentStatus = "PARTIALLY_REFUNDED"
	BookingRefunded          BookingPaymentStatus = "REFUNDED"
)

type BookingAssignmentStatus string

const (
	AssignmentAssigned   BookingAssignmentStatus = "ASSIGNED"
	AssignmentAccepted   BookingAssignmentStatus = "ACCEPTED"
	AssignmentRejected   BookingAssignmentStatus = "REJECTED"
	AssignmentCancelled  BookingAssignmentStatus = "CANCELLED"
	AssignmentReassigned BookingAssignmentStatus = "REASSIGNED"
	AssignmentCompleted  BookingAssignmentStatus = "COMPLETED"
)

type WalletTransactionType string

const (
	WalletRechargeCredit  WalletTransactionType = "RECHARGE_CREDIT"
	WalletBookingDebit    WalletTransactionType = "BOOKING_DEBIT"
	WalletBookingRefund   WalletTransactionType = "BOOKING_REFUND"
	WalletAdminCredit     WalletTransactionType = "ADMIN_CREDIT"
	WalletAdminDebit      WalletTransactionType = "ADMIN_DEBIT"
	WalletRechargeReverse WalletTransactionType = "RECHARGE_REVERSAL"
)

type WalletRechargeOrderStatus string

const (
	RechargeCreated   WalletRechargeOrderStatus = "CREATED"
	RechargePending   WalletRechargeOrderStatus = "PENDING"
	RechargePaid      WalletRechargeOrderStatus = "PAID"
	RechargeFailed    WalletRechargeOrderStatus = "FAILED"
	RechargeCancelled WalletRechargeOrderStatus = "CANCELLED"
	RechargeRefunded  WalletRechargeOrderStatus = "REFUNDED"
)

type WalletRechargePaymentStatus string

const (
	RechargePaymentAuthorized WalletRechargePaymentStatus = "AUTHORIZED"
	RechargePaymentCaptured   WalletRechargePaymentStatus = "CAPTURED"
	RechargePaymentFailed     WalletRechargePaymentStatus = "FAILED"
	RechargePaymentRefunded   WalletRechargePaymentStatus = "REFUNDED"
)

type BookingEventActorPlane string

const (
	ActorApp    BookingEventActorPlane = "app"
	ActorDriver BookingEventActorPlane = "driver"
	ActorAdmin  BookingEventActorPlane = "admin"
	ActorSystem BookingEventActorPlane = "system"
)

// ServiceSettings is the single admin-managed settings row (id=1).
// Currency amounts are stored in paise for INR, or the smallest unit for the currency.
type ServiceSettings struct {
	ID                             int16     `json:"id"`
	MinimumRechargeAmountMinor     int64     `json:"minimum_recharge_amount_minor"`
	WalletLowBalanceThresholdMinor int64     `json:"wallet_low_balance_threshold_minor"`
	Currency                       string    `json:"currency"`
	UpdatedByAdminAccountID        *string   `json:"updated_by_admin_account_id,omitempty"`
	CreatedAt                      time.Time `json:"created_at"`
	UpdatedAt                      time.Time `json:"updated_at"`
}

type CustomerProfile struct {
	AccountID         string    `json:"account_id"`
	FullName          string    `json:"full_name"`
	Phone             *string   `json:"phone,omitempty"`
	ProfilePictureKey *string   `json:"profile_picture_key,omitempty"`
	CreatedAt         time.Time `json:"created_at"`
	UpdatedAt         time.Time `json:"updated_at"`
}

type CustomerAddress struct {
	ID                string    `json:"id"`
	CustomerAccountID string    `json:"customer_account_id"`
	Label             string    `json:"label"`
	RecipientName     *string   `json:"recipient_name,omitempty"`
	RecipientPhone    *string   `json:"recipient_phone,omitempty"`
	AddressLine1      string    `json:"address_line_1"`
	AddressLine2      *string   `json:"address_line_2,omitempty"`
	Landmark          *string   `json:"landmark,omitempty"`
	Locality          *string   `json:"locality,omitempty"`
	City              string    `json:"city"`
	State             string    `json:"state"`
	PostalCode        string    `json:"postal_code"`
	Country           string    `json:"country"`
	Latitude          *float64  `json:"latitude,omitempty"`
	Longitude         *float64  `json:"longitude,omitempty"`
	IsDefault         bool      `json:"is_default"`
	CreatedAt         time.Time `json:"created_at"`
	UpdatedAt         time.Time `json:"updated_at"`
}

// CustomerVehicle describes the customer's EV only; it has no uploaded compliance documents.
type CustomerVehicle struct {
	ID                 string              `json:"id"`
	CustomerAccountID  string              `json:"customer_account_id"`
	Name               *string             `json:"name,omitempty"`
	VehicleType        CustomerVehicleType `json:"vehicle_type"`
	Make               *string             `json:"make,omitempty"`
	Model              *string             `json:"model,omitempty"`
	RegistrationNumber *string             `json:"registration_number,omitempty"`
	BatteryCapacityKWh *float64            `json:"battery_capacity_kwh,omitempty"`
	IsDefault          bool                `json:"is_default"`
	LastCharged        *time.Time          `json:"last_charged,omitempty"`
	CreatedAt          time.Time           `json:"created_at"`
	UpdatedAt          time.Time           `json:"updated_at"`
}

// Vehicle represents an electric vehicle registered in the system (compatibility model).
type Vehicle struct {
	ID            string     `json:"id"`
	CPOID         *string    `json:"cpo_id,omitempty"`
	CustomerID    string     `json:"customer_id"`
	VehicleNumber string     `json:"vehicle_number"`
	Type          *string    `json:"type,omitempty"`
	Make          *string    `json:"make,omitempty"`
	Model         *string    `json:"model,omitempty"`
	LastCharged   *time.Time `json:"last_charged,omitempty"`
	DateAdded     time.Time  `json:"date_added"`
	CreatedAt     time.Time  `json:"created_at"`
	UpdatedAt     time.Time  `json:"updated_at"`
}

type DriverProfile struct {
	AccountID                string                   `json:"account_id"`
	FullName                 string                   `json:"full_name"`
	Phone                    string                   `json:"phone"`
	DateOfBirth              *time.Time               `json:"date_of_birth,omitempty"`
	ProfilePictureKey        *string                  `json:"profile_picture_key,omitempty"`
	EmergencyContactName     *string                  `json:"emergency_contact_name,omitempty"`
	EmergencyContactPhone    *string                  `json:"emergency_contact_phone,omitempty"`
	ResidentialAddressLine1  *string                  `json:"residential_address_line_1,omitempty"`
	ResidentialAddressLine2  *string                  `json:"residential_address_line_2,omitempty"`
	ResidentialLandmark      *string                  `json:"residential_landmark,omitempty"`
	ResidentialCity          *string                  `json:"residential_city,omitempty"`
	ResidentialState         *string                  `json:"residential_state,omitempty"`
	ResidentialPostalCode    *string                  `json:"residential_postal_code,omitempty"`
	ResidentialCountry       string                   `json:"residential_country"`
	ResidentialLatitude      *float64                 `json:"residential_latitude,omitempty"`
	ResidentialLongitude     *float64                 `json:"residential_longitude,omitempty"`
	VerificationStatus       DriverVerificationStatus `json:"verification_status"`
	LifecycleStatus          DriverLifecycleStatus    `json:"lifecycle_status"`
	AvailabilityStatus       DriverAvailabilityStatus `json:"availability_status"`
	ProfileCompletedAt       *time.Time               `json:"profile_completed_at,omitempty"`
	VerificationRequestedAt  *time.Time               `json:"verification_requested_at,omitempty"`
	ReviewedByAdminAccountID *string                  `json:"reviewed_by_admin_account_id,omitempty"`
	ReviewedAt               *time.Time               `json:"reviewed_at,omitempty"`
	RejectionReason          *string                  `json:"rejection_reason,omitempty"`
	CreatedAt                time.Time                `json:"created_at"`
	UpdatedAt                time.Time                `json:"updated_at"`
}

type DriverDocument struct {
	ID                       string             `json:"id"`
	DriverAccountID          string             `json:"driver_account_id"`
	DocumentType             DriverDocumentType `json:"document_type"`
	VersionNo                int                `json:"version_no"`
	DocumentNumberCiphertext *string            `json:"-"`
	DocumentNumberLast4      *string            `json:"document_number_last4,omitempty"`
	FileStorageKey           string             `json:"file_storage_key"`
	IssuedOn                 *time.Time         `json:"issued_on,omitempty"`
	ExpiresOn                *time.Time         `json:"expires_on,omitempty"`
	Status                   DocumentStatus     `json:"status"`
	IsCurrent                bool               `json:"is_current"`
	SubmittedAt              time.Time          `json:"submitted_at"`
	ReviewedByAdminAccountID *string            `json:"reviewed_by_admin_account_id,omitempty"`
	ReviewedAt               *time.Time         `json:"reviewed_at,omitempty"`
	RejectionReason          *string            `json:"rejection_reason,omitempty"`
	CreatedAt                time.Time          `json:"created_at"`
	UpdatedAt                time.Time          `json:"updated_at"`
}

type Charger struct {
	ID                      string          `json:"id"`
	ChargerCode             string          `json:"charger_code"`
	Name                    string          `json:"name"`
	Description             *string         `json:"description,omitempty"`
	ChargerType             ChargerType     `json:"charger_type"`
	ConnectorType           *string         `json:"connector_type,omitempty"`
	MaxPowerKW              *float64        `json:"max_power_kw,omitempty"`
	SupportedVehicleTypes   json.RawMessage `json:"supported_vehicle_types"`
	ImageStorageKey         *string         `json:"image_storage_key,omitempty"`
	IsActive                bool            `json:"is_active"`
	CreatedByAdminAccountID string          `json:"created_by_admin_account_id"`
	UpdatedByAdminAccountID *string         `json:"updated_by_admin_account_id,omitempty"`
	CreatedAt               time.Time       `json:"created_at"`
	UpdatedAt               time.Time       `json:"updated_at"`
}

// ServiceVehicle is a company-owned vehicle. It deliberately contains no driver-uploaded document fields.
type ServiceVehicle struct {
	ID                      string               `json:"id"`
	VehicleCode             string               `json:"vehicle_code"`
	RegistrationNumber      string               `json:"registration_number"`
	DisplayName             string               `json:"display_name"`
	Make                    *string              `json:"make,omitempty"`
	Model                   *string              `json:"model,omitempty"`
	Color                   *string              `json:"color,omitempty"`
	OperationalStatus       ServiceVehicleStatus `json:"operational_status"`
	CurrentLatitude         *float64             `json:"current_latitude,omitempty"`
	CurrentLongitude        *float64             `json:"current_longitude,omitempty"`
	LocationUpdatedAt       *time.Time           `json:"location_updated_at,omitempty"`
	InternalNotes           *string              `json:"internal_notes,omitempty"`
	CreatedByAdminAccountID string               `json:"created_by_admin_account_id"`
	UpdatedByAdminAccountID *string              `json:"updated_by_admin_account_id,omitempty"`
	CreatedAt               time.Time            `json:"created_at"`
	UpdatedAt               time.Time            `json:"updated_at"`
}

type DistanceFareRule struct {
	ID                      string     `json:"id"`
	Name                    string     `json:"name"`
	MinDistanceKM           float64    `json:"min_distance_km"`
	MaxDistanceKM           *float64   `json:"max_distance_km,omitempty"`
	FareAmountMinor         int64      `json:"fare_amount_minor"`
	Currency                string     `json:"currency"`
	IsActive                bool       `json:"is_active"`
	ActiveFrom              time.Time  `json:"active_from"`
	ActiveUntil             *time.Time `json:"active_until,omitempty"`
	CreatedByAdminAccountID string     `json:"created_by_admin_account_id"`
	UpdatedByAdminAccountID *string    `json:"updated_by_admin_account_id,omitempty"`
	CreatedAt               time.Time  `json:"created_at"`
	UpdatedAt               time.Time  `json:"updated_at"`
}

type Wallet struct {
	ID                string    `json:"id"`
	CustomerAccountID string    `json:"customer_account_id"`
	BalanceMinor      int64     `json:"balance_minor"`
	Currency          string    `json:"currency"`
	CreatedAt         time.Time `json:"created_at"`
	UpdatedAt         time.Time `json:"updated_at"`
}

type WalletRechargeOrder struct {
	ID                   string                    `json:"id"`
	WalletID             string                    `json:"wallet_id"`
	Provider             string                    `json:"provider"`
	IdempotencyKey       string                    `json:"idempotency_key"`
	ProviderOrderID      *string                   `json:"provider_order_id,omitempty"`
	AmountMinor          int64                     `json:"amount_minor"`
	Currency             string                    `json:"currency"`
	Status               WalletRechargeOrderStatus `json:"status"`
	ProviderOrderPayload json.RawMessage           `json:"provider_order_payload"`
	ProviderCreatedAt    *time.Time                `json:"provider_created_at,omitempty"`
	PaidAt               *time.Time                `json:"paid_at,omitempty"`
	CreatedAt            time.Time                 `json:"created_at"`
	UpdatedAt            time.Time                 `json:"updated_at"`
}

type WalletRechargePayment struct {
	ID                string                      `json:"id"`
	RechargeOrderID   string                      `json:"recharge_order_id"`
	ProviderPaymentID string                      `json:"provider_payment_id"`
	ProviderOrderID   string                      `json:"provider_order_id"`
	AmountMinor       int64                       `json:"amount_minor"`
	Currency          string                      `json:"currency"`
	Status            WalletRechargePaymentStatus `json:"status"`
	PaymentMethod     *string                     `json:"payment_method,omitempty"`
	SignatureVerified bool                        `json:"signature_verified"`
	ErrorCode         *string                     `json:"error_code,omitempty"`
	ErrorDescription  *string                     `json:"error_description,omitempty"`
	ProviderPayload   json.RawMessage             `json:"provider_payload"`
	AuthorizedAt      *time.Time                  `json:"authorized_at,omitempty"`
	CapturedAt        *time.Time                  `json:"captured_at,omitempty"`
	CreatedAt         time.Time                   `json:"created_at"`
	UpdatedAt         time.Time                   `json:"updated_at"`
}

type ServiceBooking struct {
	ID                                  string               `json:"id"`
	BookingCode                         string               `json:"booking_code"`
	CustomerAccountID                   string               `json:"customer_account_id"`
	CustomerAddressID                   *string              `json:"customer_address_id,omitempty"`
	CustomerVehicleID                   *string              `json:"customer_vehicle_id,omitempty"`
	ChargerID                           string               `json:"charger_id"`
	DistanceFareRuleID                  *string              `json:"distance_fare_rule_id,omitempty"`
	AssignedDriverAccountID             *string              `json:"assigned_driver_account_id,omitempty"`
	AssignedServiceVehicleID            *string              `json:"assigned_service_vehicle_id,omitempty"`
	IdempotencyKey                      string               `json:"idempotency_key"`
	ServiceAddressText                  string               `json:"service_address_text"`
	ServiceLatitude                     *float64             `json:"service_latitude,omitempty"`
	ServiceLongitude                    *float64             `json:"service_longitude,omitempty"`
	CustomerVehicleTypeSnapshot         *string              `json:"customer_vehicle_type_snapshot,omitempty"`
	CustomerVehicleMakeSnapshot         *string              `json:"customer_vehicle_make_snapshot,omitempty"`
	CustomerVehicleModelSnapshot        *string              `json:"customer_vehicle_model_snapshot,omitempty"`
	CustomerVehicleRegistrationSnapshot *string              `json:"customer_vehicle_registration_snapshot,omitempty"`
	ChargerNameSnapshot                 string               `json:"charger_name_snapshot"`
	ChargerTypeSnapshot                 string               `json:"charger_type_snapshot"`
	DistanceKM                          float64              `json:"distance_km"`
	FareAmountMinor                     int64                `json:"fare_amount_minor"`
	Currency                            string               `json:"currency"`
	FareRuleSnapshot                    json.RawMessage      `json:"fare_rule_snapshot"`
	Status                              BookingStatus        `json:"status"`
	PaymentStatus                       BookingPaymentStatus `json:"payment_status"`
	PaidAt                              time.Time            `json:"paid_at"`
	RefundAmountMinor                   int64                `json:"refund_amount_minor"`
	CustomerNote                        *string              `json:"customer_note,omitempty"`
	AdminNote                           *string              `json:"admin_note,omitempty"`
	RequestedAt                         time.Time            `json:"requested_at"`
	AssignedAt                          *time.Time           `json:"assigned_at,omitempty"`
	DriverEnRouteAt                     *time.Time           `json:"driver_en_route_at,omitempty"`
	DriverArrivedAt                     *time.Time           `json:"driver_arrived_at,omitempty"`
	ChargingStartedAt                   *time.Time           `json:"charging_started_at,omitempty"`
	ChargingCompletedAt                 *time.Time           `json:"charging_completed_at,omitempty"`
	CompletedAt                         *time.Time           `json:"completed_at,omitempty"`
	CancelledAt                         *time.Time           `json:"cancelled_at,omitempty"`
	CancellationReason                  *string              `json:"cancellation_reason,omitempty"`
	CreatedAt                           time.Time            `json:"created_at"`
	UpdatedAt                           time.Time            `json:"updated_at"`
}

type BookingAssignment struct {
	ID                       string                  `json:"id"`
	BookingID                string                  `json:"booking_id"`
	DriverAccountID          string                  `json:"driver_account_id"`
	ServiceVehicleID         string                  `json:"service_vehicle_id"`
	AssignedByAdminAccountID string                  `json:"assigned_by_admin_account_id"`
	Status                   BookingAssignmentStatus `json:"status"`
	AssignedAt               time.Time               `json:"assigned_at"`
	AcceptedAt               *time.Time              `json:"accepted_at,omitempty"`
	EndedAt                  *time.Time              `json:"ended_at,omitempty"`
	Reason                   *string                 `json:"reason,omitempty"`
	CreatedAt                time.Time               `json:"created_at"`
	UpdatedAt                time.Time               `json:"updated_at"`
}

type WalletTransaction struct {
	ID                 string                `json:"id"`
	WalletID           string                `json:"wallet_id"`
	TransactionType    WalletTransactionType `json:"transaction_type"`
	AmountMinor        int64                 `json:"amount_minor"`
	BalanceBeforeMinor int64                 `json:"balance_before_minor"`
	BalanceAfterMinor  int64                 `json:"balance_after_minor"`
	BookingID          *string               `json:"booking_id,omitempty"`
	RechargeOrderID    *string               `json:"recharge_order_id,omitempty"`
	ActorAccountID     *string               `json:"actor_account_id,omitempty"`
	IdempotencyKey     string                `json:"idempotency_key"`
	Description        *string               `json:"description,omitempty"`
	Metadata           json.RawMessage       `json:"metadata"`
	CreatedAt          time.Time             `json:"created_at"`
}

type BookingEvent struct {
	ID             string                 `json:"id"`
	BookingID      string                 `json:"booking_id"`
	EventType      string                 `json:"event_type"`
	PreviousStatus *string                `json:"previous_status,omitempty"`
	NextStatus     *string                `json:"next_status,omitempty"`
	ActorPlane     BookingEventActorPlane `json:"actor_plane"`
	ActorAccountID *string                `json:"actor_account_id,omitempty"`
	Reason         *string                `json:"reason,omitempty"`
	Metadata       json.RawMessage        `json:"metadata"`
	CreatedAt      time.Time              `json:"created_at"`
}

type BookingServiceReport struct {
	ID                        string     `json:"id"`
	BookingID                 string     `json:"booking_id"`
	DriverAccountID           string     `json:"driver_account_id"`
	EnergyDeliveredKWh        *float64   `json:"energy_delivered_kwh,omitempty"`
	BatteryPercentageBefore   *float64   `json:"battery_percentage_before,omitempty"`
	BatteryPercentageAfter    *float64   `json:"battery_percentage_after,omitempty"`
	ServiceNotes              *string    `json:"service_notes,omitempty"`
	CompletionPhotoStorageKey *string    `json:"completion_photo_storage_key,omitempty"`
	CustomerAcknowledgedAt    *time.Time `json:"customer_acknowledged_at,omitempty"`
	CreatedAt                 time.Time  `json:"created_at"`
	UpdatedAt                 time.Time  `json:"updated_at"`
}

type DriverLocationUpdate struct {
	ID              string    `json:"id"`
	DriverAccountID string    `json:"driver_account_id"`
	BookingID       *string   `json:"booking_id,omitempty"`
	Latitude        float64   `json:"latitude"`
	Longitude       float64   `json:"longitude"`
	HeadingDegrees  *float64  `json:"heading_degrees,omitempty"`
	SpeedKPH        *float64  `json:"speed_kph,omitempty"`
	RecordedAt      time.Time `json:"recorded_at"`
}

type BookingRating struct {
	ID                string    `json:"id"`
	BookingID         string    `json:"booking_id"`
	CustomerAccountID string    `json:"customer_account_id"`
	DriverAccountID   string    `json:"driver_account_id"`
	OverallRating     int16     `json:"overall_rating"`
	DriverRating      *int16    `json:"driver_rating,omitempty"`
	ServiceRating     *int16    `json:"service_rating,omitempty"`
	ChargerRating     *int16    `json:"charger_rating,omitempty"`
	ReviewText        *string   `json:"review_text,omitempty"`
	CreatedAt         time.Time `json:"created_at"`
	UpdatedAt         time.Time `json:"updated_at"`
}

type AdminAuditLog struct {
	ID             string          `json:"id"`
	AdminAccountID string          `json:"admin_account_id"`
	Action         string          `json:"action"`
	EntityType     string          `json:"entity_type"`
	EntityID       *string         `json:"entity_id,omitempty"`
	Reason         *string         `json:"reason,omitempty"`
	BeforeSnapshot json.RawMessage `json:"before_snapshot"`
	AfterSnapshot  json.RawMessage `json:"after_snapshot"`
	CreatedAt      time.Time       `json:"created_at"`
}

