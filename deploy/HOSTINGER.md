# Site na Hostinger (VPS KVM 1, São Paulo)

O site fica no ar com HTTPS, senha e atualização diária da base. Quem tiver o endereço e a senha usa pelo navegador.

## 1. Comprar

Em hostinger.com.br, **VPS > KVM 1** (1 vCPU, 4 GB, 50 GB). Na configuração inicial escolha

| Campo | Escolha |
|---|---|
| Localização | Brasil (São Paulo) |
| Sistema | **Ubuntu 24.04** puro, sem painel |
| Senha root | Uma senha sua, guarde |

## 2. Instalar

No hPanel, abra a VPS e clique em **Terminal do navegador** (Browser terminal). Rode

```bash
curl -fsSL https://raw.githubusercontent.com/pedrorluz/ANP/main/deploy/instalar.sh -o instalar.sh
sudo bash instalar.sh
```

O script pede o usuário e a senha do site (a senha não aparece na tela). A instalação leva uns 5 minutos e a primeira carga da base outros 10.

## 3. Abrir

O endereço sai no fim da instalação e fica em `/opt/anp/ENDERECO.txt`. É o IP da VPS com traços no lugar dos pontos, mais `.sslip.io`, por exemplo https://191-101-2-3.sslip.io.

## Manutenção

```bash
cat /opt/anp/ENDERECO.txt                         # endereço do site
sudo journalctl -u anp -u anp-atualizar -n 50     # registros do site e da atualização
# trazer a versão nova do GitHub
sudo -u anp -H git -C /opt/anp/app pull && sudo -u anp -H bash -c 'cd /opt/anp/app && ~/.local/bin/uv sync --no-dev' && sudo systemctl restart anp
# trocar a senha
sudo bash /opt/anp/app/deploy/instalar.sh
```
