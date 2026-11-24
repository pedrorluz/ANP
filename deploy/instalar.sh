#!/bin/bash
# Instala o site de produção ANP num servidor Ubuntu (Hostinger, Oracle, AWS etc.).
#
# Uso normal, no terminal do servidor (pede a senha do site sem mostrar na tela):
#   curl -fsSL https://raw.githubusercontent.com/pedrorluz/ANP/main/deploy/instalar.sh -o instalar.sh
#   sudo bash instalar.sh
# Como script de inicialização (cloud-init), troque SENHA_SITE logo abaixo antes de colar.
#
# Resultado: site em https://<IP-com-tracos>.sslip.io com senha, HTTPS automático
# (Caddy + Let's Encrypt), base em /opt/anp/dados e atualização diária às 06h (Brasília).

USUARIO_SITE="${USUARIO_SITE:-anp}"
SENHA_SITE="${SENHA_SITE:-TROQUE-ESTA-SENHA}"
REPO="${REPO:-https://github.com/pedrorluz/ANP.git}"

set -euo pipefail
export DEBIAN_FRONTEND=noninteractive

set +x
if [ "$SENHA_SITE" = "TROQUE-ESTA-SENHA" ] && [ -t 0 ]; then
  read -r -p "Usuário do site [$USUARIO_SITE]: " resposta
  USUARIO_SITE="${resposta:-$USUARIO_SITE}"
  while true; do
    read -r -s -p "Senha do site: " SENHA_SITE; echo
    read -r -s -p "Repita a senha: " confirma; echo
    [ -n "$SENHA_SITE" ] && [ "$SENHA_SITE" = "$confirma" ] && break
    echo "As senhas não conferem ou estão vazias. Tente de novo."
  done
fi
if [ "$SENHA_SITE" = "TROQUE-ESTA-SENHA" ]; then
  echo "Troque SENHA_SITE antes de instalar." >&2
  exit 1
fi
set -x

# Pacotes e Caddy (proxy com HTTPS e senha)
apt-get update
apt-get install -y git curl gnupg debian-keyring debian-archive-keyring apt-transport-https iptables-persistent
curl -1sLf https://dl.cloudsmith.io/public/caddy/stable/gpg.key | gpg --dearmor --yes -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt > /etc/apt/sources.list.d/caddy-stable.list
apt-get update
apt-get install -y caddy

# Usuário de serviço, código e ambiente Python (o uv baixa o Python 3.12)
id anp >/dev/null 2>&1 || useradd --system --create-home --home-dir /opt/anp --shell /usr/sbin/nologin anp
if [ -d /opt/anp/app/.git ]; then
  sudo -u anp -H git -C /opt/anp/app pull --ff-only
else
  sudo -u anp -H git clone "$REPO" /opt/anp/app
fi
sudo -u anp -H bash -c 'command -v uv >/dev/null || [ -x ~/.local/bin/uv ] || curl -LsSf https://astral.sh/uv/install.sh | sh'
sudo -u anp -H bash -c 'cd /opt/anp/app && ~/.local/bin/uv sync --no-dev --quiet'
mkdir -p /opt/anp/dados && chown anp:anp /opt/anp/dados

# Serviço do site (só escuta em 127.0.0.1; o Caddy é a porta de entrada)
cat > /etc/systemd/system/anp.service <<UNIT
[Unit]
Description=Site de produção ANP
After=network-online.target
Wants=network-online.target

[Service]
User=anp
WorkingDirectory=/opt/anp/app
Environment=ANP_DIR_DADOS=/opt/anp/dados
Environment=PYTHONUTF8=1
ExecStart=/opt/anp/app/.venv/bin/anp servir --sem-navegador
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
UNIT

# Atualização automática da base, todo dia às 09h UTC (06h em Brasília)
cat > /etc/systemd/system/anp-atualizar.service <<UNIT
[Unit]
Description=Atualiza a base de produção ANP
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
User=anp
WorkingDirectory=/opt/anp/app
Environment=ANP_DIR_DADOS=/opt/anp/dados
Environment=PYTHONUTF8=1
ExecStart=/opt/anp/app/.venv/bin/anp atualizar --origem agendada
UNIT

cat > /etc/systemd/system/anp-atualizar.timer <<UNIT
[Unit]
Description=Atualização diária da base de produção ANP

[Timer]
OnCalendar=*-*-* 09:00:00 UTC
Persistent=true

[Install]
WantedBy=timers.target
UNIT

# Endereço público: <IP-com-tracos>.sslip.io aponta para o IP da VM e permite HTTPS sem comprar domínio
IP_PUBLICO="$(curl -s https://api.ipify.org || curl -s https://ifconfig.me)"
DOMINIO="${DOMINIO:-${IP_PUBLICO//./-}.sslip.io}"
set +x  # a senha não vai para o log do cloud-init
HASH="$(caddy hash-password --plaintext "$SENHA_SITE")"
cat > /etc/caddy/Caddyfile <<CADDY
$DOMINIO {
	encode gzip
	basic_auth {
		$USUARIO_SITE $HASH
	}
	reverse_proxy 127.0.0.1:8765 {
		header_up Host 127.0.0.1:8765
	}
}
CADDY
set -x

# Algumas imagens (a da Oracle, por exemplo) bloqueiam tudo menos SSH no firewall interno
iptables -C INPUT -p tcp --dport 80 -j ACCEPT 2>/dev/null || iptables -I INPUT 1 -p tcp --dport 80 -j ACCEPT
iptables -C INPUT -p tcp --dport 443 -j ACCEPT 2>/dev/null || iptables -I INPUT 1 -p tcp --dport 443 -j ACCEPT
netfilter-persistent save
if command -v ufw >/dev/null && ufw status | grep -q "Status: active"; then
  ufw allow 80/tcp && ufw allow 443/tcp
fi

systemctl daemon-reload
systemctl enable --now anp.service anp-atualizar.timer
systemctl restart caddy
# Primeira carga da base (uns 10 minutos), em segundo plano
systemctl start --no-block anp-atualizar.service

echo "Pronto. Site em https://$DOMINIO (usuário $USUARIO_SITE)" | tee /opt/anp/ENDERECO.txt
