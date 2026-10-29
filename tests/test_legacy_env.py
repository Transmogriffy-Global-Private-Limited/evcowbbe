import unittest
from config.legacy_env import (
    ConfigError, checked_int, derive_key, listen, postgres, public_host, smtp_mode,
)


def configured():
    return {
        'APP_ENV': 'staging', 'HTTP_LISTEN_ADDR': '127.0.0.1:18280',
        'PUBLIC_BASE_URL': 'https://dev-evcowbbe.transev.site',
        'DATABASE_URL': 'postgresql://app:secret@127.0.0.1:5432/evcowbbe?sslmode=disable',
        'PASSWORD_PEPPER': 'p'*40, 'OTP_HMAC_PEPPER': 'o'*40,
        'SMTP_TLS_MODE': 'tls',
    }

class LegacyEnvironmentContract(unittest.TestCase):
    def test_exact_existing_vps_identity(self):
        e=configured()
        self.assertEqual(postgres(e)['NAME'], 'evcowbbe')
        self.assertEqual(postgres(e)['HOST'], '127.0.0.1')
        self.assertEqual(listen(e), '127.0.0.1:18280')
        self.assertEqual(public_host(e)[0], 'dev-evcowbbe.transev.site')
        self.assertEqual(smtp_mode(e), 'tls')

    def test_key_derivation_is_stable_and_domain_separated(self):
        e=configured()
        d=derive_key(e,'django-signing')
        f=derive_key(e,'encrypted-outbox',urlsafe=True)
        self.assertEqual(derive_key(e,'django-signing'),d)
        self.assertNotEqual(d,f)
        self.assertEqual(len(d),64)
        self.assertEqual(len(f),44)
        e['OTP_HMAC_PEPPER']='q'*40
        self.assertNotEqual(derive_key(e,'django-signing'),d)

    def test_fail_closed_for_untrusted_or_invalid_config(self):
        e=configured()
        for k, v in [('PASSWORD_PEPPER','short'),('OTP_HMAC_PEPPER','REPLACE_THIS'),
                     ('PUBLIC_BASE_URL','http://example.test'),('HTTP_LISTEN_ADDR','0.0.0.0:18280'),
                     ('SMTP_TLS_MODE','plain'),('DATABASE_URL','postgresql://a:b@external.example/evcowbbe?sslmode=disable')]:
            with self.subTest(k=k,v=v):
                f=dict(e);f[k]=v
                with self.assertRaises(ConfigError):
                    {'PASSWORD_PEPPER':lambda: derive_key(f,'test'),
                     'OTP_HMAC_PEPPER':lambda: derive_key(f,'test'),
                     'PUBLIC_BASE_URL':lambda: public_host(f),
                     'HTTP_LISTEN_ADDR':lambda: listen(f),
                     'SMTP_TLS_MODE':lambda: smtp_mode(f),
                     'DATABASE_URL':lambda: postgres(f)}[k]()

    def test_strict_numeric_limits(self):
        self.assertEqual(checked_int({},'MAX',10,1,20),10)
        with self.assertRaises(ConfigError):checked_int({'MAX':'0'},'MAX',10,1,20)

if __name__=='__main__':unittest.main()
