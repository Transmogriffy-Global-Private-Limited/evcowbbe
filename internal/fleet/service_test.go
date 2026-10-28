package fleet

import (
	"bytes"
	"errors"
	"testing"
)

func TestFleetValidation(t *testing.T) {
	good := []byte(`{"asset_tag":"sv-2026-01","make":" Tata ","model":"Ace"}`)
	fields, err := ValidateFields(vehicle, good, true)
	if err != nil {
		t.Fatal(err)
	}
	if fields["asset_tag"] != "SV-2026-01" || fields["make"] != "Tata" {
		t.Fatalf("normalization mismatch: %v", fields)
	}
	cases := [][]byte{
		[]byte(`{"asset_tag":"sv1","make":"Maker"}`),
		[]byte(`{"asset_tag":"sv1","make":"Maker","model":"Model","is_active":false}`),
		[]byte(`{"asset_tag":"sv1","make":"Maker","model":"Model","operational_status":"RETIRED"}`),
		[]byte(`{"asset_tag":"sv1","make":"Maker","model":"Model","registration_valid_until":"2026-02-30"}`),
		[]byte(`{"asset_tag":"sv1","make":"Maker","model":"Model","notes":"bad\nline"}`),
		[]byte(`{"asset_tag":null,"make":"Maker","model":"Model"}`),
		[]byte(`[]`),
		[]byte(`{} {}`),
	}
	for _, body := range cases {
		if _, err = ValidateFields(vehicle, body, true); !errors.Is(err, ErrInvalid) {
			t.Fatalf("invalid body accepted: %s", body)
		}
	}
	if _, err = ValidateFields(vehicle, []byte(`{}`), false); !errors.Is(err, ErrInvalid) {
		t.Fatal("empty patch accepted")
	}
	if _, err = ValidateFields(charger, []byte(`{"rated_power_w":null}`), false); err != nil {
		t.Fatal("nullable power should be clearable", err)
	}
	if _, err = ValidateConnector([]byte(`{"connector_number":1,"connector_type":"CCS2","max_power_w":20000,"supports_ac":false,"supports_dc":false}`), true); !errors.Is(err, ErrInvalid) {
		t.Fatal("invalid connector capabilities accepted")
	}
	if _, err = ValidateConnector([]byte(`{"connector_number":1,"connector_type":"CCS2","max_power_w":20000,"supports_ac":false,"supports_dc":true}`), true); err != nil {
		t.Fatal("valid DC connector rejected", err)
	}
}

func TestFleetReasonAndMutationKey(t *testing.T) {
	for _, reason := range []string{"short", "bad\ncontrol", " ", ""} {
		if ValidReason(reason) {
			t.Fatalf("accepted unsafe reason %q", reason)
		}
	}
	if !ValidReason("Operator recorded maintenance") {
		t.Fatal("valid reason rejected")
	}
	for _, key := range []string{"short", "white space is not permitted", "contains\nnewline"} {
		if validKey(key) {
			t.Fatalf("accepted invalid key %q", key)
		}
	}
	if !validKey("12345678-1234-1234-1234-123456789abc") {
		t.Fatal("valid key rejected")
	}
	a := keyDigest("POST /vehicles", []byte(`{"x":1}`))
	b := keyDigest("POST /chargers", []byte(`{"x":1}`))
	c := keyDigest("POST /vehicles", []byte(`{"x":2}`))
	if bytes.Equal(a, b) || bytes.Equal(a, c) {
		t.Fatal("method/path and content must bind receipt")
	}
}
