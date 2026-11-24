#!/bin/bash
# Abre o site de produção ANP. No Finder, dê dois cliques neste arquivo.
# Na primeira vez instala as dependências (o uv cuida do Python), depois só abre.

cd "$(dirname "$0")" || exit 1

achar_uv() {
  UV="$(command -v uv 2>/dev/null)"
  for candidato in "$HOME/.local/bin/uv" "$HOME/.cargo/bin/uv" /opt/homebrew/bin/uv /usr/local/bin/uv; do
    [ -z "$UV" ] && [ -x "$candidato" ] && UV="$candidato"
  done
}

achar_uv
if [ -z "$UV" ]; then
  echo ""
  echo "Falta o uv, que instala o Python e as bibliotecas do projeto."
  echo "Ele vem do instalador oficial (astral.sh) e fica em ~/.local/bin."
  echo ""
  read -r -p "Instalar o uv agora? [s/n] " resposta
  if [[ "$resposta" =~ ^[sS] ]]; then
    curl -LsSf https://astral.sh/uv/install.sh | sh
    achar_uv
  fi
fi

if [ -z "$UV" ]; then
  echo ""
  echo "Sem o uv não dá para continuar. Instale com um destes comandos e abra este arquivo de novo."
  echo "    curl -LsSf https://astral.sh/uv/install.sh | sh"
  echo "    brew install uv"
  echo ""
  read -r -p "Pressione Enter para fechar."
  exit 1
fi

echo "Preparando o ambiente (só demora na primeira vez)..."
"$UV" sync --quiet || { read -r -p "Falhou ao instalar as dependências. Enter para fechar."; exit 1; }

exec "$UV" run --quiet anp servir
