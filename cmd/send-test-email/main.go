// Explicit operator-command test to an operator-provided real mailbox.
package main

import (
	"context"
	"errors"
	"flag"
	"fmt"
	"os"
	"time"

	"github.com/Transmogriffy-Global-Private-Limited/evcowbbe/internal/config"
	"github.com/Transmogriffy-Global-Private-Limited/evcowbbe/internal/mailer"
)

func main() {
	to := flag.String("to", "", "test recipient email address")
	flag.Parse()
	if *to == "" || flag.NArg() != 0 {
		fmt.Fprintln(os.Stderr, "usage: go run ./cmd/send-test-email -to you@example.com")
		os.Exit(2)
	}
	c, err := config.Load()
	if err != nil {
		fmt.Fprintln(os.Stderr, "invalid configuration")
		os.Exit(1)
	}
	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
	defer cancel()
	err = (mailer.Sender{Cfg: c}).Send(ctx, *to, "security", map[string]string{
		"Action": "An explicit SMTP connectivity test", "When": time.Now().UTC().Format(time.RFC3339),
	})
	if err != nil {
		if !errors.Is(err, context.Canceled) {
			fmt.Fprintln(os.Stderr, "SMTP test failed (credentials are not displayed)")
		}
		os.Exit(1)
	}
	fmt.Println("SMTP_SERVER_ACCEPTED_MESSAGE=YES")
	fmt.Println("Check recipient inbox to confirm final delivery")
}
