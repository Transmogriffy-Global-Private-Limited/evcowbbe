package main

import (
	"bufio"
	"context"
	"flag"
	"fmt"
	"os"
	"strings"
	"time"

	"github.com/Transmogriffy-Global-Private-Limited/evcowbbe/internal/auth"
	"github.com/Transmogriffy-Global-Private-Limited/evcowbbe/internal/config"
	"github.com/Transmogriffy-Global-Private-Limited/evcowbbe/internal/database"
)

func main() {
	email := flag.String("email", "", "email address of new admin")
	flag.Parse()
	if *email == "" || flag.NArg() != 0 {
		fmt.Fprintln(os.Stderr, "usage: bootstrap-admin -email admin@example.com (password from stdin)")
		os.Exit(2)
	}
	fmt.Fprintln(os.Stderr, "Enter the new admin password through standard input (never use CLI arguments).")
	reader := bufio.NewReader(os.Stdin)
	password, err := reader.ReadString('\n')
	if err != nil {
		fmt.Fprintln(os.Stderr, "password input unavailable")
		os.Exit(2)
	}
	password = strings.TrimRight(password, "\r\n")
	c, err := config.Load()
	if err != nil {
		fmt.Fprintln(os.Stderr, "invalid configuration")
		os.Exit(1)
	}
	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
	defer cancel()
	db, err := database.Open(ctx, c.DatabaseURL, c.DBMaxConns)
	if err != nil {
		fmt.Fprintln(os.Stderr, "database unavailable")
		os.Exit(1)
	}
	defer db.Close()
	if err = auth.New(db, c).BootstrapAdmin(ctx, *email, password); err != nil {
		fmt.Fprintln(os.Stderr, "admin creation refused:", err)
		os.Exit(1)
	}
	fmt.Println("Admin account created; email marked verified by trusted local operator. Login on /api/v1/admin/auth/login")
}
