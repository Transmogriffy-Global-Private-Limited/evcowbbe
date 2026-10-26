package mailer

import (
	"context"
	"crypto/tls"
	"errors"
	"net"
	"net/smtp"
	"strconv"
	"time"
)

// Probe checks the live remote SMTP handshake and configured authentication.
// It never sends an email. Delivery must be tested separately by an operator.
func (s Sender) Probe(ctx context.Context) error {
	c := s.Cfg
	address := net.JoinHostPort(c.SMTPHost, strconv.Itoa(c.SMTPPort))
	dialer := net.Dialer{Timeout: 5 * time.Second}
	var conn net.Conn
	var err error
	if c.SMTPTLSMode == "tls" {
		conn, err = tls.DialWithDialer(&dialer, "tcp", address, &tls.Config{
			ServerName: c.SMTPHost, MinVersion: tls.VersionTLS12,
		})
	} else {
		conn, err = dialer.DialContext(ctx, "tcp", address)
	}
	if err != nil {
		return errors.New("SMTP connection unavailable")
	}
	defer conn.Close()
	if deadline, ok := ctx.Deadline(); ok {
		_ = conn.SetDeadline(deadline)
	} else {
		_ = conn.SetDeadline(time.Now().Add(10 * time.Second))
	}
	client, err := smtp.NewClient(conn, c.SMTPHost)
	if err != nil {
		return errors.New("SMTP handshake failed")
	}
	defer client.Close()
	if c.SMTPTLSMode == "starttls" {
		supported, _ := client.Extension("STARTTLS")
		if !supported {
			return errors.New("SMTP STARTTLS unavailable")
		}
		if err := client.StartTLS(&tls.Config{ServerName: c.SMTPHost, MinVersion: tls.VersionTLS12}); err != nil {
			return errors.New("SMTP STARTTLS failed")
		}
	}
	if c.SMTPUser != "" {
		if c.SMTPTLSMode == "none" {
			return errors.New("SMTP authentication without TLS forbidden")
		}
		if err := client.Auth(smtp.PlainAuth("", c.SMTPUser, c.SMTPPassword, c.SMTPHost)); err != nil {
			return errors.New("SMTP authentication failed")
		}
	}
	if err := client.Quit(); err != nil {
		return errors.New("SMTP session shutdown failed")
	}
	return nil
}
