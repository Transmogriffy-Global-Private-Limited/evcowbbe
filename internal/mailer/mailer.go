package mailer

import (
	"bytes"
	"context"
	"crypto/tls"
	"embed"
	"errors"
	"html/template"
	"io"
	"mime"
	"net"
	"net/mail"
	"net/smtp"
	"strconv"
	"strings"
	texttemplate "text/template"
	"time"

	"github.com/Transmogriffy-Global-Private-Limited/evcowbbe/internal/config"
)

//go:embed templates/*
var templates embed.FS

type Sender struct{ Cfg config.Config }

func (s Sender) Send(ctx context.Context, to, kind string, params map[string]string) error {
	if _, err := mail.ParseAddress(to); err != nil || strings.ContainsAny(to, "\r\n") {
		return errors.New("invalid recipient")
	}
	if kind != "verification" && kind != "otp" && kind != "reset" && kind != "security" {
		return errors.New("unrecognized mail template")
	}
	input := map[string]string{}
	for k, v := range params {
		input[k] = v
	}
	render := func(extension string) (string, error) {
		raw, err := templates.ReadFile("templates/" + kind + "." + extension)
		if err != nil {
			return "", err
		}
		var b bytes.Buffer
		if extension == "html" {
			t, err := template.New(kind).Option("missingkey=error").Parse(string(raw))
			if err != nil {
				return "", err
			}
			err = t.Execute(&b, input)
			return b.String(), err
		}
		t, err := texttemplate.New(kind).Option("missingkey=error").Parse(string(raw))
		if err != nil {
			return "", err
		}
		err = t.Execute(&b, input)
		return b.String(), err
	}
	text, err := render("txt")
	if err != nil {
		return err
	}
	html, err := render("html")
	if err != nil {
		return err
	}
	subjects := map[string]string{"verification": "Verify your Charger on Wheels email", "otp": "Your Charger on Wheels security code", "reset": "Reset your Charger on Wheels password", "security": "Charger on Wheels security notice"}
	return s.sendSMTP(ctx, to, subjects[kind], text, html)
}
func (s Sender) sendSMTP(ctx context.Context, to, subject, plain, html string) error {
	c := s.Cfg
	if strings.ContainsAny(c.MailFrom, "\r\n") || strings.ContainsAny(c.MailFromName, "\r\n") {
		return errors.New("invalid SMTP sender")
	}
	from, err := mail.ParseAddress(c.MailFrom)
	if err != nil || from.Address != c.MailFrom {
		return errors.New("invalid MAIL_FROM_ADDRESS")
	}
	boundary := "cow-alt-2026" // constant MIME boundary cannot appear in fixed template context prefix
	var buf bytes.Buffer
	fromHeader := (&mail.Address{Name: c.MailFromName, Address: c.MailFrom}).String()
	buf.WriteString("From: " + fromHeader + "\r\nTo: " + to + "\r\nSubject: " + mime.QEncoding.Encode("utf-8", subject) + "\r\nMIME-Version: 1.0\r\n")
	buf.WriteString("Content-Type: multipart/alternative; boundary=\"" + boundary + "\"\r\n\r\n")
	for _, part := range []struct{ kind, body string }{{"text/plain", plain}, {"text/html", html}} {
		buf.WriteString("--" + boundary + "\r\nContent-Type: " + part.kind + "; charset=utf-8\r\nContent-Transfer-Encoding: 8bit\r\n\r\n")
		buf.WriteString(strings.ReplaceAll(strings.ReplaceAll(part.body, "\r\n", "\n"), "\n", "\r\n"))
		buf.WriteString("\r\n")
	}
	buf.WriteString("--" + boundary + "--\r\n")
	address := net.JoinHostPort(c.SMTPHost, strconv.Itoa(c.SMTPPort))
	dialer := net.Dialer{Timeout: 5 * time.Second}
	var conn net.Conn
	if c.SMTPTLSMode == "tls" {
		conn, err = tls.DialWithDialer(&dialer, "tcp", address, &tls.Config{ServerName: c.SMTPHost, MinVersion: tls.VersionTLS12})
	} else {
		conn, err = dialer.DialContext(ctx, "tcp", address)
	}
	if err != nil {
		return errors.New("SMTP connection unavailable")
	}
	defer conn.Close()
	if deadline, ok := ctx.Deadline(); ok {
		conn.SetDeadline(deadline)
	} else {
		conn.SetDeadline(time.Now().Add(10 * time.Second))
	}
	client, err := smtp.NewClient(conn, c.SMTPHost)
	if err != nil {
		return errors.New("SMTP handshake failed")
	}
	defer client.Close()
	if c.SMTPTLSMode == "starttls" {
		ok, _ := client.Extension("STARTTLS")
		if !ok {
			return errors.New("SMTP STARTTLS unavailable")
		}
		if err = client.StartTLS(&tls.Config{ServerName: c.SMTPHost, MinVersion: tls.VersionTLS12}); err != nil {
			return errors.New("SMTP STARTTLS failed")
		}
	}
	if c.SMTPUser != "" {
		if c.SMTPTLSMode == "none" {
			return errors.New("SMTP authentication without TLS forbidden")
		}
		if err = client.Auth(smtp.PlainAuth("", c.SMTPUser, c.SMTPPassword, c.SMTPHost)); err != nil {
			return errors.New("SMTP authentication failed")
		}
	}
	if err = client.Mail(c.MailFrom); err != nil {
		return errors.New("SMTP rejected sender")
	}
	if err = client.Rcpt(to); err != nil {
		return errors.New("SMTP rejected recipient")
	}
	writer, err := client.Data()
	if err != nil {
		return errors.New("SMTP data request failed")
	}
	if _, err = io.Copy(writer, &buf); err != nil {
		writer.Close()
		return errors.New("SMTP message write failed")
	}
	if err = writer.Close(); err != nil {
		return errors.New("SMTP delivery failed")
	}
	return client.Quit()
}
