"""
rodar.py — SellersFlow Price Tracker
Coleta preços a cada X minutos e sobe pro GitHub automaticamente.
"""

import os, sys, time, subprocess
from pathlib import Path
from datetime import datetime

PASTA  = Path(__file__).parent
CONFIG = PASTA / "config.env"
REPO   = PASTA.parent

def log(msg):
    ts = datetime.now().strftime("%d/%m %H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)

def carregar_config():
    config = {}
    if CONFIG.exists():
        for linha in CONFIG.read_text(encoding="utf-8").splitlines():
            if "=" in linha and not linha.startswith("#"):
                k, v = linha.split("=", 1)
                config[k.strip()] = v.strip()
    return config

def set_env(config):
    for k, v in config.items():
        if k != "INTERVALO_MINUTOS":
            os.environ[k] = v

def rodar_scraper():
    scraper = PASTA / "scraper.py"
    result = subprocess.run([sys.executable, str(scraper)], cwd=str(PASTA))
    return result.returncode == 0

def run(cmd):
    return subprocess.run(cmd, cwd=str(REPO), capture_output=True, text=True)

def git_push():
    run(["git", "config", "user.name",  "price-bot"])
    run(["git", "config", "user.email", "bot@sellersflow.com"])

    # Pull remoto antes de commitar
    run(["git", "fetch", "origin"])

    run(["git", "add", "data/prices.json"])

    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    commit = run(["git", "commit", "-m", f"data: {ts}"])

    if "nothing to commit" in commit.stdout + commit.stderr:
        log("Git: sem alterações")
        return True

    # Force push — scraper local é a fonte de verdade dos dados
    push = run(["git", "push", "--force"])
    if push.returncode == 0:
        log("✅ GitHub atualizado")
        return True

    log(f"⚠️  Push falhou: {push.stderr[:100]}")
    return False

def main():
    print("=" * 55)
    print("  SellersFlow Price Tracker — Modo Local")
    print("  Feche esta janela para parar.")
    print("=" * 55)

    config = carregar_config()
    if not config:
        log("❌ config.env não encontrado!")
        input("Aperte Enter para fechar...")
        return

    set_env(config)
    intervalo = int(config.get("INTERVALO_MINUTOS", 15)) * 60

    log(f"Coleta a cada {intervalo//60} minutos")
    log(f"MELI: {'✅' if config.get('MELI_TOKEN') else '❌'}")
    print()

    rodada = 0
    while True:
        rodada += 1
        log(f"━━━ Rodada #{rodada} ━━━━━━━━━━━━━━━━━━━━━")
        if rodar_scraper():
            git_push()
        else:
            log("⚠️  Scraper com erro")
        proxima = datetime.fromtimestamp(time.time() + intervalo)
        log(f"Próxima: {proxima.strftime('%H:%M:%S')}")
        print()
        time.sleep(intervalo)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nParado.")
    except Exception as e:
        print(f"\nErro: {e}")
        input("Enter para fechar...")
