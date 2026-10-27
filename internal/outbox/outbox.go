// Package outbox provides encrypted, transactionally enqueued authentication
// email with a recoverable, at-least-once SMTP delivery worker.
package outbox

import (
	"context"
	"crypto/aes"
	"crypto/cipher"
	"crypto/hmac"
	"crypto/rand"
	"crypto/sha256"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"strings"
	"time"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
)

type Sender interface {
	Send(context.Context, string, string, map[string]string) error
}

type Store struct {
	Pool *pgxpool.Pool
	Key  []byte
	Mail Sender
}

// The pre-existing random OTP pepper derives a separately domain-separated
// encryption key. Rotation requires draining or explicitly retiring queued mail.
// Neither the key nor plaintext tokens are ever stored in PostgreSQL.
func New(pool *pgxpool.Pool, otpPepper []byte, mail Sender) *Store {
	h := hmac.New(sha256.New, otpPepper)
	h.Write([]byte("evcowbbe/auth-mail-outbox/aes-256-gcm/v1"))
	return &Store{Pool: pool, Key: h.Sum(nil), Mail: mail}
}

type Credential struct {
	Kind        string
	Digest      []byte
	ChallengeID string
	ExpiresAt   time.Time
}

func (s *Store) encrypt(recipient, kind string, params map[string]string) ([]byte, []byte, error) {
	raw, err := json.Marshal(params)
	if err != nil || len(raw) > 4096 {
		return nil, nil, errors.New("invalid outbox payload")
	}
	block, err := aes.NewCipher(s.Key)
	if err != nil {
		return nil, nil, err
	}
	aead, err := cipher.NewGCM(block)
	if err != nil {
		return nil, nil, err
	}
	nonce := make([]byte, aead.NonceSize())
	if _, err = io.ReadFull(rand.Reader, nonce); err != nil {
		return nil, nil, err
	}
	return aead.Seal(nil, nonce, raw, []byte(kind+"\x00"+recipient)), nonce, nil
}

func (s *Store) decrypt(recipient, kind string, ciphertext, nonce []byte) (map[string]string, error) {
	if len(ciphertext) == 0 || len(nonce) != 12 {
		return nil, errors.New("invalid encrypted mail")
	}
	block, err := aes.NewCipher(s.Key)
	if err != nil {
		return nil, err
	}
	aead, err := cipher.NewGCM(block)
	if err != nil {
		return nil, err
	}
	plain, err := aead.Open(nil, nonce, ciphertext, []byte(kind+"\x00"+recipient))
	if err != nil {
		return nil, errors.New("mail authentication failed")
	}
	var payload map[string]string
	err = json.Unmarshal(plain, &payload)
	for i := range plain {
		plain[i] = 0
	}
	return payload, err
}

func (s *Store) Queue(ctx context.Context, tx pgx.Tx, account, recipient, kind string, params map[string]string, source Credential) error {
	if len(s.Key) != 32 || len(recipient) < 3 || len(recipient) > 254 || strings.ContainsAny(recipient, "\r\n") {
		return errors.New("invalid outbox configuration")
	}
	switch kind {
	case "verification":
		if source.Kind != "VERIFY_EMAIL" || len(source.Digest) != 32 || source.ExpiresAt.IsZero() {
			return errors.New("invalid verification source")
		}
	case "reset":
		if source.Kind != "RESET_PASSWORD" || len(source.Digest) != 32 || source.ExpiresAt.IsZero() {
			return errors.New("invalid reset source")
		}
	case "otp":
		if source.Kind != "OTP" || len(source.Digest) != 32 || source.ChallengeID == "" || source.ExpiresAt.IsZero() {
			return errors.New("invalid OTP source")
		}
	case "security":
		if source.Kind != "" {
			return errors.New("invalid security notice source")
		}
	default:
		return errors.New("invalid outbox template")
	}
	// Unique nonce and AEAD authentication; context-bound template + recipient.
	ciphertext, nonce, err := s.encrypt(recipient, kind, params)
	if err != nil {
		return err
	}
	var challenge any
	if source.ChallengeID != "" {
		challenge = source.ChallengeID
	}
	var expiry any
	if !source.ExpiresAt.IsZero() {
		expiry = source.ExpiresAt
	}
	_, err = tx.Exec(ctx, `INSERT INTO auth_mail_outbox
 (id,account_id,recipient_email,template,payload_ciphertext,payload_nonce,credential_kind,credential_digest,credential_challenge_id,expires_at)
 VALUES(gen_random_uuid(),$1,$2,$3,$4,$5,NULLIF($6,''),$7,$8,$9)`, account, recipient, kind, ciphertext, nonce, source.Kind, nullableDigest(source.Digest), challenge, expiry)
	return err
}

func nullableDigest(d []byte) any {
	if len(d) == 0 {
		return nil
	}
	return d
}

type entry struct {
	id, lease, recipient, template, credentialKind string
	challenge                                      *string
	data, nonce, digest                            []byte
	expiry                                         *time.Time
	attempts                                       int
}

// DeliverBatch claims rows under SKIP LOCKED, then releases the transaction
// *before* making any network call. Leases recover after worker/process crashes.
// A provider accepting mail before the acknowledgement transaction commits can
// cause a duplicate; this contract is at-least-once, never exactly-once.
func (s *Store) DeliverBatch(ctx context.Context, limit int) (int, error) {
	if limit < 1 || limit > 100 {
		return 0, errors.New("invalid outbox batch")
	}
	count := 0
	for i := 0; i < limit; i++ {
		e, ok, err := s.claim(ctx)
		if err != nil {
			return count, err
		}
		if !ok {
			break
		}
		count++
		s.deliver(ctx, e)
	}
	return count, nil
}

func (s *Store) claim(ctx context.Context) (entry, bool, error) {
	tx, err := s.Pool.Begin(ctx)
	if err != nil {
		return entry{}, false, err
	}
	defer tx.Rollback(ctx)
	var e entry
	err = tx.QueryRow(ctx, `SELECT id::text,recipient_email,template,payload_ciphertext,payload_nonce,
 COALESCE(credential_kind,''),credential_digest,credential_challenge_id::text,expires_at,attempts
 FROM auth_mail_outbox WHERE
 (state='PENDING' AND next_attempt_at<=now()) OR (state='LEASED' AND lease_until<=now())
 ORDER BY next_attempt_at,created_at FOR UPDATE SKIP LOCKED LIMIT 1`).Scan(
		&e.id, &e.recipient, &e.template, &e.data, &e.nonce, &e.credentialKind, &e.digest, &e.challenge, &e.expiry, &e.attempts)
	if errors.Is(err, pgx.ErrNoRows) {
		return entry{}, false, nil
	}
	if err != nil {
		return entry{}, false, err
	}
	e.lease, err = randomUUID()
	if err != nil {
		return entry{}, false, err
	}
	_, err = tx.Exec(ctx, `UPDATE auth_mail_outbox SET state='LEASED',lease_token=$2,lease_until=now()+interval '30 seconds',attempts=attempts+1,updated_at=now() WHERE id=$1`, e.id, e.lease)
	if err != nil {
		return entry{}, false, err
	}
	if err = tx.Commit(ctx); err != nil {
		return entry{}, false, err
	}
	e.attempts++
	return e, true, nil
}

func randomUUID() (string, error) {
	var b [16]byte
	if _, err := rand.Read(b[:]); err != nil {
		return "", err
	}
	b[6] = (b[6] & 0x0f) | 0x40
	b[8] = (b[8] & 0x3f) | 0x80
	const h = "0123456789abcdef"
	out := make([]byte, 36)
	j := 0
	for i, v := range b {
		if i == 4 || i == 6 || i == 8 || i == 10 {
			out[j] = '-'
			j++
		}
		out[j] = h[v>>4]
		out[j+1] = h[v&15]
		j += 2
	}
	return string(out), nil
}

func (s *Store) validSource(ctx context.Context, e entry) (bool, error) {
	if e.expiry != nil && !e.expiry.After(time.Now()) {
		return false, nil
	}
	switch e.credentialKind {
	case "":
		return true, nil
	case "VERIFY_EMAIL", "RESET_PASSWORD":
		var exists bool
		if err := s.Pool.QueryRow(ctx, `SELECT EXISTS(SELECT 1 FROM auth_action_tokens t JOIN accounts a ON a.id=t.account_id WHERE t.digest=$1 AND t.kind=$2 AND t.consumed_at IS NULL AND t.expires_at>now() AND a.disabled_at IS NULL)`, e.digest, e.credentialKind).Scan(&exists); err != nil {
			return false, err
		}
		return exists, nil
	case "OTP":
		if e.challenge == nil {
			return false, nil
		}
		var exists bool
		if err := s.Pool.QueryRow(ctx, `SELECT EXISTS(SELECT 1 FROM auth_challenges c JOIN accounts a ON a.id=c.account_id WHERE c.id=$1 AND c.digest=$2 AND c.consumed_at IS NULL AND c.expires_at>now() AND a.disabled_at IS NULL)`, *e.challenge, e.digest).Scan(&exists); err != nil {
			return false, err
		}
		return exists, nil
	default:
		return false, nil
	}
}

func (s *Store) deliver(ctx context.Context, e entry) {
	valid, err := s.validSource(ctx, e)
	if err != nil {
		s.retry(ctx, e, "database_unavailable")
		return
	}
	if !valid {
		s.finish(ctx, e, "EXPIRED", "source_expired")
		return
	}
	payload, err := s.decrypt(e.recipient, e.template, e.data, e.nonce)
	if err != nil {
		s.finish(ctx, e, "DEAD", "decrypt_failed")
		return
	}
	mailCtx, cancel := context.WithTimeout(ctx, 12*time.Second)
	defer cancel()
	if s.Mail == nil {
		s.retry(ctx, e, "sender_unavailable")
		return
	}
	if err = s.Mail.Send(mailCtx, e.recipient, e.template, payload); err != nil {
		s.retry(ctx, e, "smtp_failure")
		return
	}
	s.finish(ctx, e, "SENT", "")
}

func (s *Store) finish(ctx context.Context, e entry, state, reason string) {
	// A late worker must never overwrite a newer lease owner's result.
	_, _ = s.Pool.Exec(ctx, `UPDATE auth_mail_outbox SET state=$3,payload_ciphertext=NULL,payload_nonce=NULL,
 lease_token=NULL,lease_until=NULL,last_error_class=NULLIF($4,''),sent_at=CASE WHEN $3='SENT' THEN now() ELSE sent_at END,updated_at=now()
 WHERE id=$1 AND lease_token=$2 AND state='LEASED'`, e.id, e.lease, state, reason)
}

func (s *Store) retry(ctx context.Context, e entry, reason string) {
	if e.attempts >= 12 || (e.expiry != nil && !e.expiry.After(time.Now().Add(30*time.Second))) {
		s.finish(ctx, e, "DEAD", reason)
		return
	}
	delay := time.Duration(1<<min(e.attempts, 9)) * time.Second
	_, _ = s.Pool.Exec(ctx, `UPDATE auth_mail_outbox SET state='PENDING',next_attempt_at=now()+$3::interval,
 lease_token=NULL,lease_until=NULL,last_error_class=$4,updated_at=now()
 WHERE id=$1 AND lease_token=$2 AND state='LEASED'`, e.id, e.lease, fmt.Sprintf("%d seconds", int(delay.Seconds())), reason)
}

func (s *Store) Run(ctx context.Context, log *slog.Logger) {
	ticker := time.NewTicker(5 * time.Second)
	defer ticker.Stop()
	for {
		// Process already pending messages at startup; do not wait for the first tick.
		n, err := s.DeliverBatch(ctx, 20)
		if err != nil && ctx.Err() == nil && log != nil {
			log.Warn("email outbox polling unavailable", "kind", "database")
		}
		if n >= 20 {
			continue
		}
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
		}
	}
}
