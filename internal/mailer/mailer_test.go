package mailer

import (
	"bufio"
	"context"
	"fmt"
	"net"
	"net/mail"
	"strconv"
	"strings"
	"testing"
	"time"

	"github.com/Transmogriffy-Global-Private-Limited/evcowbbe/internal/config"
)

// This SMTP receiver is an in-process test double, not Mailpit or a local
// service dependency. It captures a single message without contacting the
// configured real SMTP provider or delivering external mail.
func captureSMTP(t *testing.T) (config.Config, <-chan string) {
	t.Helper()
	ln, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = ln.Close() })
	out := make(chan string, 1)
	go func() {
		conn, err := ln.Accept()
		if err != nil {
			return
		}
		defer conn.Close()
		_ = conn.SetDeadline(time.Now().Add(10 * time.Second))
		reader := bufio.NewReader(conn)
		writer := bufio.NewWriter(conn)
		send := func(line string) bool {
			if _, err := writer.WriteString(line + "\r\n"); err != nil {
				return false
			}
			return writer.Flush() == nil
		}
		if !send("220 local-test ESMTP ready") {
			return
		}
		for {
			line, err := reader.ReadString('\n')
			if err != nil {
				return
			}
			verb := strings.ToUpper(strings.TrimSpace(line))
			switch {
			case strings.HasPrefix(verb, "EHLO "), strings.HasPrefix(verb, "HELO "):
				if !send("250 local-test") {
					return
				}
			case strings.HasPrefix(verb, "MAIL FROM:"), strings.HasPrefix(verb, "RCPT TO:"):
				if !send("250 accepted") {
					return
				}
			case verb == "DATA":
				if !send("354 send message") {
					return
				}
				var message strings.Builder
				for {
					content, readErr := reader.ReadString('\n')
					if readErr != nil {
						return
					}
					if content == ".\r\n" {
						break
					}
					message.WriteString(content)
				}
				out <- message.String()
				if !send("250 queued") {
					return
				}
			case verb == "QUIT":
				_ = send("221 bye")
				return
			default:
				_ = send("500 unsupported in fake SMTP receiver")
				return
			}
		}
	}()
	host, portString, err := net.SplitHostPort(ln.Addr().String())
	if err != nil {
		t.Fatal(err)
	}
	port, err := strconv.Atoi(portString)
	if err != nil {
		t.Fatal(err)
	}
	return config.Config{
		SMTPHost: host, SMTPPort: port, SMTPTLSMode: "none",
		MailFrom: "noreply@example.test", MailFromName: "TransEV",
	}, out
}

func TestTemplatesAndMIMEAgainstInProcessSMTP(t *testing.T) {
	for _, kind := range []string{"verification", "otp", "reset", "security"} {
		t.Run(kind, func(t *testing.T) {
			cfg, received := captureSMTP(t)
			sender := Sender{Cfg: cfg}
			params := map[string]string{
				"Code": "test-code-456", "Action": "<script>alert('x')</script>",
				"Minutes": "10", "When": "2026-10-26T00:00:00Z",
			}
			ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
			defer cancel()
			if err := sender.Send(ctx, "receiver@example.test", kind, params); err != nil {
				t.Fatal(err)
			}
			select {
			case message := <-received:
				parsed, err := mail.ReadMessage(strings.NewReader(message))
				if err != nil {
					t.Fatalf("invalid RFC 5322 email message: %v", err)
				}
				from, err := mail.ParseAddress(parsed.Header.Get("From"))
				if err != nil || from.Name != "TransEV" || from.Address != "noreply@example.test" {
					t.Fatal("incorrect structured From address")
				}
				to, err := mail.ParseAddress(parsed.Header.Get("To"))
				if err != nil || to.Address != "receiver@example.test" {
					t.Fatal("incorrect structured To address")
				}
				for _, required := range []string{
					"multipart/alternative",
					"Content-Type: text/plain",
					"Content-Type: text/html",
				} {
					if !strings.Contains(message, required) {
						t.Fatalf("missing required MIME component: %s", required)
					}
				}
				if kind != "security" && !strings.Contains(message, "test-code-456") {
					t.Fatal("code missing from rendered email")
				}
				if kind == "otp" {
					if !strings.Contains(message, "&lt;script&gt;") {
						t.Fatal("HTML template did not escape injected markup")
					}
					if strings.Contains(message, "<p>Your <script>") {
						t.Fatal("unescaped markup in HTML part")
					}
				}
			case <-time.After(5 * time.Second):
				t.Fatal("fake SMTP did not capture message")
			}
		})
	}
}

func TestMailerRejectsInvalidInputsBeforeDial(t *testing.T) {
	sender := Sender{Cfg: config.Config{
		SMTPHost: "127.0.0.1", SMTPPort: 1,
		MailFrom: "noreply@example.test", MailFromName: "TransEV",
	}}
	tests := []struct {
		name, to, kind string
		params         map[string]string
	}{
		{"newline recipient", "target@example.test\r\nBcc:bad@example.test", "otp", map[string]string{"Code": "123456", "Action": "login", "Minutes": "10"}},
		{"unknown template", "target@example.test", "unexpected", map[string]string{}},
		{"missing template data", "target@example.test", "otp", map[string]string{"Code": "123456"}},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			err := sender.Send(context.Background(), tc.to, tc.kind, tc.params)
			if err == nil {
				t.Fatal("invalid email was accepted")
			}
		})
	}
	sender.Cfg.MailFromName = fmt.Sprintf("TransEV\r\n%s", "Bcc:bad@example.test")
	if err := sender.Send(context.Background(), "target@example.test", "security",
		map[string]string{"Action": "notice", "When": "now"}); err == nil {
		t.Fatal("header injection was accepted")
	}
}
