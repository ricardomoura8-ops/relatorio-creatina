# Monitor de Preços · SellersFlow

Painel ao vivo com scraper automático via GitHub Actions.

## Como funciona

```
GitHub Actions (a cada hora)
  └── scraper/scraper.py coleta preços das URLs em scraper/links.json
       └── grava data/prices.json no repositório
            └── index.html lê data/prices.json e renderiza ao vivo
```

## Setup (3 passos)

### 1. Suba os arquivos no GitHub
Upload desta pasta inteira no seu repositório `relatorio-creatina`.

### 2. Ative o GitHub Pages
Settings → Pages → Branch: main → Folder: / (root)

### 3. Habilite o GitHub Actions
Actions → ativar workflows → rodar manualmente uma vez para testar.

## Arquivos

| Arquivo | Função |
|---|---|
| `index.html` | Painel principal |
| `scraper/scraper.py` | Coleta preços (Amazon, MELI, Magalu, Site) |
| `scraper/links.json` | 303 URLs dos seus produtos |
| `data/prices.json` | Gerado automaticamente pelo scraper |
| `.github/workflows/scrape.yml` | Agenda coleta toda hora |

## Adicionar produtos

Edite `scraper/links.json` e adicione:
```json
{
  "id": "dux-creatina-600g-amazon",
  "label": "DUX Creatina 600g",
  "group": "DUX",
  "url": "https://www.amazon.com.br/dp/XXXXXXXX"
}
```

O painel atualiza automaticamente na próxima coleta.
