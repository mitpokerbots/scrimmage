#!/bin/bash
# Shibboleth SP setup for MIT Touchstone, following
# https://wikis.mit.edu/confluence/display/TOUCHSTONE/Provisioning+Steps+for+Shibboleth+SP
# (this does what MIT's mit-config-shib.sh does, without the prompts).
#
#   sudo /opt/scrimmage/deploy/touchstone.sh registration
#       Print the email to send to touchstone-support@mit.edu.
#   sudo /opt/scrimmage/deploy/touchstone.sh okta <EXTERNAL_ID>
#       Switch to the Okta integration once touchstone-support sends its ID.
#   sudo /opt/scrimmage/deploy/touchstone.sh status
#
# Until the Okta ID is set, the SP runs MIT's legacy-IdP configuration: shibd
# runs and serves https://DOMAIN/Shibboleth.sso/Metadata, which is what
# touchstone-support checks before registering the site, but logins won't
# succeed yet.
set -euo pipefail

# shellcheck source=/dev/null
source /etc/scrimmage/install.env
: "${DOMAIN:?} ${CONTACT_EMAIL:?}"

STATE=/srv/shibboleth            # persistent: survives instance rebuilds
CONF=/etc/shibboleth
MIT=https://touchstone.mit.edu/config/shibboleth-sp-3
MD_CERT_URL=https://touchstone.mit.edu/certs/mit-md-cert.pem
MD_CERT_SHA256=0B:65:DE:D2:38:47:48:08:D4:1D:19:EF:10:6E:DF:74:29:BD:D6:1C:DB:FA:65:34:70:A6:B5:A5:2A:74:74:8C

okta_id() { cat "$STATE/okta-id" 2>/dev/null || true; }

fetch() {  # fetch URL DEST: download, falling back to the last good copy
  local tmp
  tmp=$(mktemp)
  if curl -fsSL --max-time 30 "$1" -o "$tmp" && [ -s "$tmp" ]; then
    mv "$tmp" "$2"
  else
    rm -f "$tmp"
    [ -s "$2" ] || { echo "Could not download $1" >&2; exit 1; }
    echo "warning: could not download $1; using the saved copy" >&2
  fi
}

ensure_keys() {
  # MIT requires dedicated self-signed signing and encryption certificates
  # whose CN is the web server's host name.
  for use in signing encrypt; do
    if [ ! -s "$STATE/sp-$use-key.pem" ]; then
      openssl req -x509 -newkey rsa:3072 -nodes -days 3650 -sha256 \
        -subj "/CN=$DOMAIN" \
        -addext "subjectAltName=DNS:$DOMAIN,URI:https://$DOMAIN/shibboleth" \
        -keyout "$STATE/sp-$use-key.pem" -out "$STATE/sp-$use-cert.pem" 2>/dev/null
    fi
    install -m 0644 -o root -g root "$STATE/sp-$use-cert.pem" "$CONF/sp-$use-cert.pem"
    install -m 0600 -o _shibd -g _shibd "$STATE/sp-$use-key.pem" "$CONF/sp-$use-key.pem"
  done
}

configure() {
  install -d -m 0700 "$STATE"
  ensure_keys
  fetch "$MIT/shibboleth2.xml.in" "$STATE/shibboleth2.xml.in"
  fetch "$MIT/attribute-map.xml" "$STATE/attribute-map.xml"
  install -m 0644 "$STATE/attribute-map.xml" "$CONF/attribute-map.xml"

  local id begin_okta end_okta begin_legacy end_legacy
  id=$(okta_id)
  if [ -n "$id" ]; then
    fetch "https://touchstone.mit.edu/okta/idp/metadata/$id" "$STATE/okta-$id-md.xml"
    install -m 0644 "$STATE/okta-$id-md.xml" "$CONF/okta-$id-md.xml"
    begin_okta='<!-- Begin Okta IdP addition -->'; end_okta='<!-- End Okta IdP addition -->'
    begin_legacy='<!--'; end_legacy='-->'
  else
    id=XXXXXX
    fetch "$MD_CERT_URL" "$STATE/mit-md-cert.pem"
    fingerprint=$(openssl x509 -noout -fingerprint -sha256 -in "$STATE/mit-md-cert.pem" | sed 's/^[^=]*=//')
    if [ "$fingerprint" != "$MD_CERT_SHA256" ]; then
      echo "MIT metadata certificate fingerprint mismatch; contact touchstone-support" >&2
      exit 1
    fi
    install -m 0644 "$STATE/mit-md-cert.pem" "$CONF/mit-md-cert.pem"
    begin_okta='<!--'; end_okta='-->'
    begin_legacy='<!-- Begin Legacy IdP addition -->'; end_legacy='<!-- End Legacy IdP addition -->'
  fi

  sed -e "s:%%HOSTNAME%%:$DOMAIN:" \
      -e "s:%%SSLDIR%%:/lib:" \
      -e "s:%%CONTACT_EMAIL%%:$CONTACT_EMAIL:" \
      -e "s:%%BEGIN_OKTA%%:$begin_okta:" -e "s:%%END_OKTA%%:$end_okta:" \
      -e "s:%%OKTA_EXT_ID%%:$id:" \
      -e "s:%%BEGIN_LEGACY%%:$begin_legacy:" -e "s:%%END_LEGACY%%:$end_legacy:" \
      -e "s:%%BEGIN_INCOMMON%%:<!--:" -e "s:%%END_INCOMMON%%:-->:" \
      -e "s:@-PKGXMLDIR-@:/usr/share/xml/shibboleth:" \
      -e "s:@-PKGSYSCONFDIR-@:/etc/shibboleth:" \
      -e "s:@-LIBEXECDIR-@:/usr/libexec:" \
      -e "s:@-VARRUNDIR-@:/var/run:" \
      -e "s:@-PREFIX-@:/usr:" \
      "$STATE/shibboleth2.xml.in" > "$CONF/shibboleth2.xml.new"
  mv "$CONF/shibboleth2.xml.new" "$CONF/shibboleth2.xml"
  chmod 0644 "$CONF/shibboleth2.xml"
  if ! shibd -t >/tmp/shibd-check.log 2>&1; then
    cat /tmp/shibd-check.log >&2
    echo "shibd rejected the generated configuration" >&2
    exit 1
  fi
}

restart() {
  systemctl restart shibd
  systemctl reload apache2 2>/dev/null || true
}

status() {
  local id
  id=$(okta_id)
  echo "Shibboleth entity ID: https://$DOMAIN/shibboleth"
  echo "SP metadata:          https://$DOMAIN/Shibboleth.sso/Metadata"
  if [ -n "$id" ]; then
    echo "Touchstone:           Okta integration $id (logins enabled)"
  else
    echo "Touchstone:           not registered yet. Next steps:"
    echo "  1. Point DNS for $DOMAIN at this server and wait for https://$DOMAIN/ to load."
    echo "  2. Email touchstone-support@mit.edu the text from:"
    echo "       sudo $0 registration"
    echo "  3. When they reply with an Okta external ID, run:"
    echo "       sudo $0 okta <EXTERNAL_ID>"
    echo "  Meanwhile, log in with:  sudo scrimmage login-link <kerberos>"
  fi
}

registration() {
  ensure_keys >/dev/null
  cat <<EOF
To: touchstone-support@mit.edu
Subject: Touchstone (Okta) registration for $DOMAIN

Hello,

Please register this Shibboleth SP with the MIT Okta IdP. The SP is installed
and running; its metadata is at https://$DOMAIN/Shibboleth.sso/Metadata.

- Contact email (for the MIT metadata): $CONTACT_EMAIL
- Web server host name: $DOMAIN
- Entity ID: https://$DOMAIN/shibboleth
- InCommon: no, MIT users only
- Organization: MIT Pokerbots (student activity)
- Organization URL: https://pokerbots.org
- Application URL: https://$DOMAIN/
- Attributes: eduPersonPrincipalName (user ID) and displayName
- User access: all MIT users
- Platform: Ubuntu 24.04, Apache 2.4, Shibboleth SP 3 (libapache2-mod-shib)

SP signing certificate (sp-signing-cert.pem):
$(cat "$STATE/sp-signing-cert.pem")

SP encryption certificate (sp-encrypt-cert.pem):
$(cat "$STATE/sp-encrypt-cert.pem")

Thank you!
EOF
}

case "${1:-status}" in
  configure) configure ;;
  okta)
    id="${2:-}"
    if ! [[ "$id" =~ ^[[:alnum:]]+$ ]]; then
      echo "usage: $0 okta <EXTERNAL_ID>   (letters and digits, from touchstone-support)" >&2
      exit 2
    fi
    install -d -m 0700 "$STATE"
    previous=$(okta_id)
    echo "$id" > "$STATE/okta-id"
    if ! (configure); then
      if [ -n "$previous" ]; then echo "$previous" > "$STATE/okta-id"; else rm -f "$STATE/okta-id"; fi
      exit 1
    fi
    restart
    echo "Touchstone login is enabled. Try https://$DOMAIN/login"
    ;;
  registration) registration ;;
  status) status ;;
  *) echo "usage: $0 {status|registration|okta <EXTERNAL_ID>|configure}" >&2; exit 2 ;;
esac
