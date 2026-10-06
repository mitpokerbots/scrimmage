#!/bin/bash
# Runs the real installer in a throwaway Ubuntu 24.04 container (only systemd and
# Docker stubbed), then exercises Apache + mod_shib + shibd + gunicorn with MIT's
# real Touchstone templates. No AWS needed.
#
#   docker run --rm -v "$PWD:/src:ro" ubuntu:24.04 bash /src/deploy/test-deploy.sh
#
# Checks the pre-registration (legacy) and Okta Shibboleth configurations, the
# TLS/proxy/static setup, the private worker API, and that only Apache can set
# the login identity.
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
step() { echo; echo "######## $*"; }
check() { if eval "$2"; then echo "PASS: $1"; else echo "FAIL: $1"; FAILED=1; fi; }
FAILED=0

step "run deploy/install.sh"
apt-get update -qq && apt-get install -yqq git curl >/dev/null
cp -a /src /opt/scrimmage && rm -rf /opt/scrimmage/.venv /opt/scrimmage/data
# On servers root clones the repo; the mount keeps the host user's uid, which git refuses.
chown -R root:root /opt/scrimmage
mkdir -p /stubs /etc/scrimmage
for command in systemctl docker; do
  printf '#!/bin/sh\necho "[stub] %s $*"\n' "$command" > "/stubs/$command"
  chmod +x "/stubs/$command"
done
cat > /etc/scrimmage/install.env <<'EOF'
DOMAIN=scrimmage.example.org
ADMINS=boss
CONTACT_EMAIL=pokerbots@mit.edu
EOF
PATH=/stubs:$PATH /opt/scrimmage/deploy/install.sh > /tmp/install.log 2>&1 \
  || { cat /tmp/install.log; exit 1; }
COMMIT=$(git -C /opt/scrimmage rev-parse HEAD)
export COMMIT
check "cli works as the service user" "scrimmage migrate | grep -q 'schema version 1'"
check "website user has no docker group" "! id -nG scrimmage | grep -qw docker"
check "worker user cannot read the database" "! setpriv --reuid=scrimmage-worker --regid=scrimmage-worker --init-groups cat /srv/scrimmage/db/scrimmage.sqlite3 >/dev/null 2>&1"
check "shibd -t accepts MIT's legacy config" "shibd -t >/dev/null 2>&1"
check "SP cert CN is the domain" "openssl x509 -in /etc/shibboleth/sp-signing-cert.pem -noout -subject | grep -q scrimmage.example.org"
check "SP keys kept on the data volume" "[ -s /srv/shibboleth/sp-encrypt-key.pem ]"
check "deployed commit recorded" "[ \"\$(cat /var/lib/scrimmage-deploy/deployed)\" = \"\$(git -C /opt/scrimmage rev-parse HEAD)\" ]"

step "redeploy (what update.sh and autodeploy run)"
redeploy_start=$(date +%s)
PATH=/stubs:$PATH /opt/scrimmage/deploy/install.sh > /tmp/redeploy.log 2>&1 \
  || { tail -40 /tmp/redeploy.log; exit 1; }
echo "redeploy took $(( $(date +%s) - redeploy_start ))s"
check "redeploy skips package installs" "grep -q 'Packages already installed' /tmp/redeploy.log"
check "redeploy leaves shibd alone when its config is unchanged" "! grep -q 'systemctl restart shibd' /tmp/redeploy.log"

step "start shibd, gunicorn, apache"
# No public DNS here, so stand in a self-signed certificate for Let's Encrypt.
apt-get install -yqq ssl-cert >/dev/null
sed -i -e 's|^MDomain .*|# MDomain disabled for the test|' \
  -e 's|SSLEngine on|SSLEngine on\n    SSLCertificateFile /etc/ssl/certs/ssl-cert-snakeoil.pem\n    SSLCertificateKeyFile /etc/ssl/private/ssl-cert-snakeoil.key|' \
  /etc/apache2/sites-available/scrimmage.conf
mkdir -p /run/shibboleth && chown _shibd:_shibd /run/shibboleth
setpriv --reuid=_shibd --regid=_shibd --init-groups shibd -f &
install -d -m 0750 -o scrimmage -g scrimmage /run/scrimmage
(set -a; . /etc/scrimmage/env; set +a
 setpriv --reuid=scrimmage --regid=scrimmage --init-groups \
   /opt/scrimmage/.venv/bin/gunicorn --bind unix:/run/scrimmage/web.sock --umask 0007 \
   --workers 2 --threads 4 --worker-class gthread --daemon 'scrimmage.web:create_app()')
apache2ctl start
sleep 4

H=(-sk --resolve scrimmage.example.org:443:127.0.0.1 --resolve scrimmage.example.org:80:127.0.0.1)
U=https://scrimmage.example.org
check "http redirects to https" "curl -s -o /dev/null -w '%{redirect_url}' http://scrimmage.example.org/ --resolve scrimmage.example.org:80:127.0.0.1 | grep -q '^https://scrimmage.example.org/'"
check "home page via unix socket (off-season countdown)" "curl ${H[*]} $U/ | grep -q 'data-countdown'"
check "healthz" "[ \"\$(curl ${H[*]} $U/healthz)\" = ok ]"
check "HSTS header" "curl ${H[*]} -I $U/ | grep -qi strict-transport-security"
check "static served by apache with caching" "curl ${H[*]} -I $U/static/site.css | grep -qi 'cache-control: public'"
check "SP metadata served" "curl ${H[*]} $U/Shibboleth.sso/Metadata | grep -q 'entityID=\"https://scrimmage.example.org/shibboleth\"'"
LOC=$(curl "${H[@]}" -o /dev/null -w '%{redirect_url}' "$U/auth/touchstone")
echo "legacy login redirect: ${LOC:0:120}..."
check "login redirects to MIT discovery" "[[ '$LOC' == https://wayf.mit.edu/DS* ]]"
check "/login sends to /auth/touchstone" "curl ${H[*]} -o /dev/null -w '%{redirect_url}' '$U/login?next=/team' | grep -q '/auth/touchstone'"
check "worker API blocked on the public site" "[ \"\$(curl ${H[*]} -o /dev/null -w '%{http_code}' $U/api/worker/version)\" = 403 ]"
check "worker API served internally" "curl -s http://127.0.0.1:8080/api/worker/version | grep -q \$COMMIT"
check "internal port serves nothing else" "[ \"\$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8080/)\" = 403 ]"
check "worker API needs the token" "[ \"\$(curl -s -o /dev/null -w '%{http_code}' -X POST http://127.0.0.1:8080/api/worker/claim)\" = 401 ]"
TOKEN=$(cat /srv/scrimmage/worker_token)
export TOKEN
check "worker API accepts the token" "curl -s -X POST -H \"Authorization: Bearer \$TOKEN\" -H 'X-Worker: w1' -H \"X-Scrimmage-Commit: \$COMMIT\" -H 'Content-Type: application/json' -d '{\"total\": {\"cores\": 1, \"memory_mb\": 8000}, \"free\": {\"cores\": 1, \"memory_mb\": 8000}}' http://127.0.0.1:8080/api/worker/claim | grep -q games"
check "spoofed header ignored without shib session" "[ \"\$(curl ${H[*]} -o /dev/null -w '%{http_code}' -H 'X-Remote-User: boss@mit.edu' $U/auth/touchstone)\" = 302 ]"

step "shibboleth: Okta config (fake IdP metadata, as if touchstone-support replied)"
openssl req -x509 -newkey rsa:2048 -nodes -days 30 -subj /CN=fake-okta -keyout /tmp/idp.key -out /tmp/idp.crt 2>/dev/null
CERT=$(grep -v CERTIFICATE /tmp/idp.crt | tr -d '\n')
cat > /srv/shibboleth/okta-FAKE123-md.xml <<EOF
<?xml version="1.0"?>
<md:EntityDescriptor xmlns:md="urn:oasis:names:tc:SAML:2.0:metadata" entityID="http://www.okta.com/FAKE123">
  <md:IDPSSODescriptor WantAuthnRequestsSigned="false" protocolSupportEnumeration="urn:oasis:names:tc:SAML:2.0:protocol">
    <md:KeyDescriptor use="signing"><ds:KeyInfo xmlns:ds="http://www.w3.org/2000/09/xmldsig#"><ds:X509Data><ds:X509Certificate>$CERT</ds:X509Certificate></ds:X509Data></ds:KeyInfo></md:KeyDescriptor>
    <md:SingleSignOnService Binding="urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect" Location="https://okta.mit.edu/app/fake/sso/saml"/>
  </md:IDPSSODescriptor>
</md:EntityDescriptor>
EOF
echo FAKE123 > /srv/shibboleth/okta-id
/opt/scrimmage/deploy/touchstone.sh configure 2>&1 | grep -v '^$' || true
check "okta config accepted" "shibd -t >/dev/null 2>&1"
check "okta SSO entity configured" "grep -q 'http://www.okta.com/FAKE123' /etc/shibboleth/shibboleth2.xml"
pkill shibd; sleep 1
setpriv --reuid=_shibd --regid=_shibd --init-groups shibd -f &
sleep 4
apache2ctl graceful; sleep 2
LOC=$(curl "${H[@]}" -o /dev/null -w '%{redirect_url}' "$U/auth/touchstone")
echo "okta login redirect: ${LOC:0:120}..."
check "login redirects to Okta with a SAMLRequest" "[[ '$LOC' == https://okta.mit.edu/app/fake/sso/saml?SAMLRequest=* ]]"
/opt/scrimmage/deploy/touchstone.sh status
/opt/scrimmage/deploy/touchstone.sh registration | head -12

step "identity header plumbing (Basic auth standing in for Shibboleth)"
htpasswd -bc /etc/apache2/test.htpasswd 'alice@mit.edu' pw >/dev/null 2>&1 || {
  apt-get install -yqq apache2-utils >/dev/null; htpasswd -bc /etc/apache2/test.htpasswd 'alice@mit.edu' pw >/dev/null; }
/opt/scrimmage/.venv/bin/python - <<'EOF'
import re
p = "/etc/apache2/sites-available/scrimmage.conf"
s = open(p).read()
s = re.sub(r"AuthType shibboleth\n.*?Require shib-session\n",
           "AuthType Basic\n        AuthName test\n        AuthUserFile /etc/apache2/test.htpasswd\n"
           "        Require expr \"-n %{REMOTE_USER}\"\n        SetEnvIf Request_URI . \"displayName=Alice Ng\"\n",
           s, flags=re.S)
open(p, "w").write(s)
EOF
apache2ctl configtest >/dev/null 2>&1 && apache2ctl graceful; sleep 2
check "unauthenticated login refused" "[ \"\$(curl ${H[*]} -o /dev/null -w '%{http_code}' $U/auth/touchstone)\" = 401 ]"
curl "${H[@]}" -c /tmp/jar -o /dev/null -u 'alice@mit.edu:pw' -H 'X-Remote-User: boss@mit.edu' "$U/auth/touchstone"
PAGE=$(curl "${H[@]}" -b /tmp/jar "$U/")
export PAGE  # read by the eval'd checks below
check "logged in as the authenticated user, not the spoofed one" "echo \"\$PAGE\" | grep -q 'Log out alice' && ! echo \"\$PAGE\" | grep -q 'boss'"
check "display name passed through" "[ \"\$(sqlite3 /srv/scrimmage/db/scrimmage.sqlite3 \"SELECT display_name FROM users WHERE kerberos = 'alice'\")\" = 'Alice Ng' ]"
check "secure session cookie" "grep -q 'TRUE.*scrimmage_session' /tmp/jar"

step "result"
if [ "$FAILED" != 0 ]; then
  echo "SOME CHECKS FAILED"
  exit 1
fi
echo "ALL CHECKS PASSED"
