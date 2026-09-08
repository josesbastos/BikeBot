# Bike Price Alert 🚴

Bot para procurar bicicletas **endurance / all-round** até **1.800 €**, em tamanho **L ou equivalente**, e enviar um email para:

**josesampaio2@gmail.com**

O bot **não liga ao Gmail**, não lê emails e não pede password do Gmail.

## Bicicletas monitorizadas

- Orbea Orca M30 — 57
- CUBE Attain C:62 — 60
- Giant Defy Advanced — L / XL
- Merida Scultura Endurance — L / XL / 58 / 59
- Trek Domane — 58 / 60
- Cannondale Synapse Carbon — 58 / 61
- Scott Addict — 58 / XL
- Canyon Endurace CF — L / XL

Tudo pode ser alterado em `config.yaml`.

## Lojas e OLX

O bot filtra resultados para uma lista de lojas portuguesas e lojas europeias com mercado/envio para Portugal.
A lista está em `config.yaml` e é fácil acrescentar novos domínios.

Também pesquisa anúncios individuais do `olx.pt`. Nos emails, os resultados ficam
separados entre **lojas/vendedores profissionais** e **vendedores particulares**.
Páginas gerais de pesquisa do OLX não são tratadas como anúncios.

O resumo inclui anúncios OLX de 2023 ou mais recentes mesmo quando o tamanho não
é o pretendido. Esses anúncios aparecem marcados como **Fora do tamanho
pretendido** e nunca geram um alerta de compra. Como anúncios usados podem ter
preços inferiores aos das lojas, o limite mínimo de leitura do OLX é 300 €.

## Como funciona

1. De 3 em 3 horas, o GitHub Actions executa `monitor.py`.
2. O bot pesquisa cada modelo na web.
3. Só considera domínios permitidos.
4. Abre a página, tenta ler preço, disponibilidade e tamanho.
5. Só alerta quando:
   - preço <= 1.800 €;
   - aparece um tamanho pretendido;
   - o ano do modelo é 2023 ou mais recente;
   - a página não está marcada globalmente como esgotada.
6. Guarda as ofertas já notificadas em `data/seen_offers.json`.
7. Não volta a avisar da mesma oferta, exceto se o preço baixar ou voltar a stock.
8. Se nenhuma bicicleta cumprir todos os critérios, envia um resumo agrupado por
   loja com os preços, tamanhos, stock e links que conseguiu confirmar.

> O stock por tamanho nem sempre é exposto de forma estruturada pelos sites.
> Por isso, o email inclui sempre o link para confirmares a variante antes da compra.

---

# Instalação no GitHub

## 1. Criar um repositório

Cria um repositório privado no GitHub, por exemplo:

`bike-price-alert`

Extrai este ZIP e coloca os ficheiros no repositório.

## 2. Criar conta Resend

Cria uma conta no Resend **com `josesampaio2@gmail.com`**.

Isto é importante: usando o domínio de teste `onboarding@resend.dev`, o Resend permite enviar emails para o **mesmo endereço associado à conta Resend** sem precisares de comprar/verificar um domínio próprio.

Não há ligação ao Gmail.

## 3. Criar API key

No Resend:

**API Keys → Create API Key**

Copia a key (`re_...`).

Não coloques a key no código.

## 4. Guardar a API key no GitHub

No repositório:

**Settings → Secrets and variables → Actions → New repository secret**

Nome:

`RESEND_API_KEY`

Valor:

`re_...`

## 5. Ativar e testar

Abre:

**Actions → Bike price alert → Run workflow**

Na primeira execução, se já existir uma oferta válida, receberás um email.

Depois disso, o workflow corre automaticamente **de 3 em 3 horas**.

---

# Testar sem enviar email

Localmente:

Linux / macOS:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

DRY_RUN=1 python monitor.py
```

Windows PowerShell:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt

$env:DRY_RUN="1"
python monitor.py
```

O modo `DRY_RUN` não envia emails nem marca ofertas como já notificadas.

## Credenciais locais

Preenche o ficheiro `.env` na raiz do projeto:

```dotenv
RESEND_API_KEY=re_COLOCA_A_TUA_CHAVE_AQUI
RESEND_FROM="Bike Alert <onboarding@resend.dev>"
```

O `.env` é carregado automaticamente e está no `.gitignore`, portanto não é
enviado para o GitHub. O ficheiro `.env.example` contém apenas um modelo seguro.

Para testar envio:

```bash
export RESEND_API_KEY="re_..."
python monitor.py
```

## Alterar preço máximo

Em `config.yaml`:

```yaml
max_price_eur: 1800
```

## Email quando não existem ofertas válidas

O resumo de preços está ativo por defeito e é enviado sempre que uma execução
não encontra nenhuma bicicleta dentro do orçamento:

```yaml
send_no_match_summary: true
```

Altera para `false` se quiseres receber apenas alertas de ofertas válidas.

## Ano mínimo

Só aparecem bicicletas cujo ano de modelo seja identificado e cumpra:

```yaml
min_model_year: 2023
require_model_year: true
```

Assim, bicicletas anteriores a 2023 e anúncios sem ano identificável são excluídos.

O limite mínimo específico para marketplaces pode ser alterado em:

```yaml
marketplace_min_price_eur: 300
```

## Adicionar uma bicicleta

```yaml
- name: "Specialized Roubaix"
  aliases:
    - "Specialized Roubaix"
  sizes: ["58", "61"]
```

## Adicionar uma loja

Em `allowed_domains`:

```yaml
- "exemplo.pt"
```

## Frequência

Por defeito corre de 3 em 3 horas:

```yaml
- cron: "17 */3 * * *"
```

O agendamento está em `.github/workflows/bike-alert.yml`.
