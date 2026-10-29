package ent

import (
	"reflect"
	"testing"
)

// Newly generated legacy Ent entities must not expose credential material if
// an entity is ever accidentally passed to a JSON response/log serializer.
func TestCredentialsAreExcludedFromEntityJSON(t *testing.T) {
	for _, tc := range []struct {
		entity any
		field  string
	}{
		{Account{}, "PasswordHash"},
		{AuthSession{}, "AccessDigest"},
		{AuthSession{}, "RefreshDigest"},
		{ConsumedRefreshToken{}, "Digest"},
		{AuthActionToken{}, "Digest"},
		{AuthChallenge{}, "Digest"},
		{AuthRateLimit{}, "KeyDigest"},
		{AuthMailOutbox{}, "PayloadCiphertext"},
		{AuthMailOutbox{}, "PayloadNonce"},
		{AuthMailOutbox{}, "CredentialDigest"},
		{MutationIdempotencyReceipt{}, "RequestDigest"},
	} {
		f, ok := reflect.TypeOf(tc.entity).FieldByName(tc.field)
		if !ok || f.Tag.Get("json") != "-" {
			t.Errorf("%T.%s must have json:\"-\"", tc.entity, tc.field)
		}
	}
}
