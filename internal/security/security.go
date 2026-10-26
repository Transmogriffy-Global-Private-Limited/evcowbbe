package security

import (
	"crypto/hmac"
	"crypto/rand"
	"crypto/sha256"
	"crypto/subtle"
	"encoding/base64"
	"encoding/hex"
	"errors"
	"fmt"
	"net/mail"
	"regexp"
	"strconv"
	"strings"
	"unicode/utf8"

	"golang.org/x/crypto/argon2"
)

var emailRe = regexp.MustCompile(`^[a-z0-9.!#$%&'*+/=?^_\x60{|}~-]+@[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?\.[a-z]{2,}$`)

func Email(v string) (string, error) {
	v = strings.ToLower(strings.TrimSpace(v))
	if len(v) > 254 || len(v) < 3 || !emailRe.MatchString(v) {
		return "", errors.New("invalid email")
	}
	addr, err := mail.ParseAddress(v)
	if err != nil || addr.Address != v || addr.Name != "" {
		return "", errors.New("invalid email")
	}
	return v, nil
}
func PasswordValid(password string) error {
	if len(password) < 12 || len(password) > 256 || !utf8.ValidString(password) {
		return errors.New("password must contain 12-256 UTF-8 bytes")
	}
	weak := strings.ToLower(password)
	for _, word := range []string{"password123", "qwerty123", "123456789012", "letmein123", "adminadmin", "changeme123"} {
		if strings.Contains(weak, word) {
			return errors.New("password is too common")
		}
	}
	return nil
}
func RandomToken() (string, error) {
	b := make([]byte, 32)
	if _, err := rand.Read(b); err != nil {
		return "", err
	}
	return base64.RawURLEncoding.EncodeToString(b), nil
}
func UUID() (string, error) {
	b := make([]byte, 16)
	if _, err := rand.Read(b); err != nil {
		return "", err
	}
	b[6] = (b[6] & 0x0f) | 0x40
	b[8] = (b[8] & 0x3f) | 0x80
	return fmt.Sprintf("%x-%x-%x-%x-%x", b[:4], b[4:6], b[6:8], b[8:10], b[10:]), nil
}
func OTP() (string, error) {
	b := make([]byte, 4)
	if _, err := rand.Read(b); err != nil {
		return "", err
	}
	var x uint32 = uint32(b[0])<<24 | uint32(b[1])<<16 | uint32(b[2])<<8 | uint32(b[3])
	limit := uint64(1) << 32
	limit -= limit % 1000000
	// Reject biased values by drawing again.
	for uint64(x) >= limit {
		if _, err := rand.Read(b); err != nil {
			return "", err
		}
		x = uint32(b[0])<<24 | uint32(b[1])<<16 | uint32(b[2])<<8 | uint32(b[3])
	}
	return fmt.Sprintf("%06d", x%1000000), nil
}
func Digest(v string) []byte { sum := sha256.Sum256([]byte(v)); return sum[:] }
func Mac(pepper []byte, parts ...string) []byte {
	h := hmac.New(sha256.New, pepper)
	for _, s := range parts {
		h.Write([]byte(strconv.Itoa(len(s))))
		h.Write([]byte(":"))
		h.Write([]byte(s))
	}
	return h.Sum(nil)
}
func Equal(a, b []byte) bool { return subtle.ConstantTimeCompare(a, b) == 1 }
func HashPassword(password string, pepper []byte, mem uint32, it uint32, p uint8) (string, error) {
	salt := make([]byte, 16)
	if _, err := rand.Read(salt); err != nil {
		return "", err
	}
	material := Mac(pepper, password)
	hash := argon2.IDKey(material, salt, it, mem, p, 32)
	return fmt.Sprintf("$argon2id$v=19$m=%d,t=%d,p=%d$%s$%s", mem, it, p, base64.RawStdEncoding.EncodeToString(salt), base64.RawStdEncoding.EncodeToString(hash)), nil
}
func CheckPassword(password, encoded string, pepper []byte) (bool, error) {
	parts := strings.Split(encoded, "$")
	if len(parts) != 6 || parts[1] != "argon2id" || parts[2] != "v=19" {
		return false, errors.New("invalid stored password encoding")
	}
	var m, t int
	var p int
	_, err := fmt.Sscanf(parts[3], "m=%d,t=%d,p=%d", &m, &t, &p)
	if err != nil || m < 65536 || m > 262144 || t < 3 || t > 10 || p < 1 || p > 4 {
		return false, errors.New("invalid stored password parameters")
	}
	salt, err := base64.RawStdEncoding.DecodeString(parts[4])
	if err != nil || len(salt) != 16 {
		return false, errors.New("invalid stored salt")
	}
	expected, err := base64.RawStdEncoding.DecodeString(parts[5])
	if err != nil || len(expected) != 32 {
		return false, errors.New("invalid stored hash")
	}
	actual := argon2.IDKey(Mac(pepper, password), salt, uint32(t), uint32(m), uint8(p), 32)
	return Equal(actual, expected), nil
}
func ConstantEmailKey(pepper []byte, email string) string {
	return hex.EncodeToString(Mac(pepper, "limiter", email))
}
