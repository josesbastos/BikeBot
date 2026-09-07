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

## Lojas

O bot filtra resultados para uma lista de lojas portuguesas e lojas europeias com mercado/envio para Portugal.
A lista está em `config.yaml` e é fácil acrescentar novos domínios.

## Como funciona

1. De 3 em 3 horas, o GitHub Actions executa `monitor.py`.
2. O bot pesquisa cada modelo na web.
3. Só considera domínios permitidos.
4. Abre a página, tenta ler preço, disponibilidade e tamanho.
5. Só alerta quando:
   - preço <= 1.800 €;
   - aparece um tamanho pretendido;
   - a página não está marcada globalmente como esgotada.
6. Guarda as ofertas já notificadas em `data/seen_offers.json`.
7. Não volta a avisar da mesma oferta, exceto se o preço baixar ou voltar a stock.

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
