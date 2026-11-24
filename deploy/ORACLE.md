# Site na Oracle Cloud (Always Free)

O site fica no ar numa máquina grátis da Oracle, com HTTPS, senha e atualização diária da base. Quem tiver o endereço e a senha usa pelo navegador, sem instalar nada.

## 1. Conta

Crie a conta em cloud.oracle.com/free. Na escolha da região principal (home region), pegue **Brazil East (São Paulo)** ou **Brazil Southeast (Vinhedo)**. Ela não muda depois, e os recursos grátis só existem nela.

## 2. Máquina

Em **Compute > Instances > Create instance**

| Campo | Escolha |
|---|---|
| Image | Canonical Ubuntu 24.04 |
| Shape | Ampere, VM.Standard.A1.Flex, 2 OCPUs e 12 GB de memória (é o limite grátis) |
| Networking | Deixe criar a rede nova e marque **Assign a public IPv4 address** |
| SSH keys | **Save private key** e guarde o arquivo (serve para manutenção) |
| Show advanced options > Management | Em **Paste cloud-init script**, cole o conteúdo de `deploy/instalar.sh` depois de trocar `SENHA_SITE` (e, se quiser, `USUARIO_SITE`) |

Se aparecer **Out of capacity**, tente outro Availability domain, 1 OCPU com 6 GB, ou de novo mais tarde.

## 3. Portas

Em **Networking > Virtual cloud networks**, abra a rede da máquina, **Security Lists > Default Security List > Add Ingress Rules**

| Source CIDR | IP Protocol | Destination Port Range |
|---|---|---|
| 0.0.0.0/0 | TCP | 80,443 |

## 4. Endereço

Copie o **Public IP address** da máquina, por exemplo 152.67.10.20. O site fica em https://152-67-10-20.sslip.io (o IP com traços no lugar dos pontos). A instalação leva uns 10 minutos depois que a máquina liga, e a primeira carga da base outros 10. O navegador pede usuário e senha.

## Manutenção (por SSH, com a chave salva)

```bash
ssh -i chave.key ubuntu@IP_DA_MAQUINA
cat /opt/anp/ENDERECO.txt                         # endereço do site
sudo tail -50 /var/log/cloud-init-output.log      # andamento da instalação
sudo journalctl -u anp -u anp-atualizar -n 50     # registros do site e da atualização
# trazer a versão nova do GitHub
sudo -u anp -H git -C /opt/anp/app pull && sudo -u anp -H bash -c 'cd /opt/anp/app && ~/.local/bin/uv sync --no-dev' && sudo systemctl restart anp
# trocar a senha
sudo USUARIO_SITE=anp SENHA_SITE='nova-senha' bash /opt/anp/app/deploy/instalar.sh
```
