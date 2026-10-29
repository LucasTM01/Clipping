"""
Configura o perfil Chrome dedicado ao pipeline para acesso a sites protegidos.

Cria um perfil Chrome separado (~/.clipping/pipeline_profile/) que NUNCA é
usado pelo Chrome normal, eliminando o problema de profile lock quando o
Chrome do usuário está aberto durante o pipeline das 07:00.

Uso (único, uma vez):
    python -m tools.setup_pipeline_browser

O script abre o Chrome com o perfil dedicado. O usuário deve:
  1. Fazer login em reuters.com (e bloomberg.com, se necessário)
  2. Fechar o Chrome quando terminar

A partir daí, o pipeline usa esse perfil automaticamente como fallback
quando o Chrome normal está bloqueado.

Para resetar (apagar o perfil e recomeçar):
    python -m tools.setup_pipeline_browser --reset
"""

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

PIPELINE_PROFILE = Path.home() / ".clipping" / "pipeline_profile"

_SITES_TO_LOGIN = [
    "https://www.reuters.com",
    "https://www.bloomberg.com",
]


def _find_chrome_exe() -> str | None:
    candidates = [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        str(Path.home() / "AppData" / "Local" / "Google" / "Chrome" / "Application" / "chrome.exe"),
    ]
    for path in candidates:
        if Path(path).exists():
            return path
    return None


def setup(reset: bool = False) -> None:
    if reset and PIPELINE_PROFILE.exists():
        print(f"Removendo perfil existente em {PIPELINE_PROFILE} ...")
        shutil.rmtree(PIPELINE_PROFILE)
        print("Perfil removido.")

    PIPELINE_PROFILE.mkdir(parents=True, exist_ok=True)

    chrome = _find_chrome_exe()
    if not chrome:
        print("[ERRO] Chrome nao encontrado. Instale o Google Chrome e tente novamente.")
        sys.exit(1)

    print(f"""
╔══════════════════════════════════════════════════════════════════╗
║         CONFIGURAÇÃO DO PERFIL DE PIPELINE — CLIPPING           ║
╠══════════════════════════════════════════════════════════════════╣
║                                                                  ║
║  O Chrome vai abrir com um perfil separado (sem suas abas).     ║
║                                                                  ║
║  Você deve:                                                      ║
║    1. Fazer login em reuters.com                                 ║
║    2. Fazer login em bloomberg.com (se usar)                    ║
║    3. Fechar o Chrome quando terminar                            ║
║                                                                  ║
║  Perfil será salvo em:                                           ║
║    {str(PIPELINE_PROFILE):<62}║
║                                                                  ║
║  Após fechar o Chrome, o pipeline usará este perfil              ║
║  automaticamente quando o seu Chrome normal estiver aberto.      ║
╚══════════════════════════════════════════════════════════════════╝
""")

    input("Pressione ENTER para abrir o Chrome...")

    # Abre a primeira URL diretamente
    args = [
        chrome,
        f"--user-data-dir={PIPELINE_PROFILE}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-blink-features=AutomationControlled",
    ] + _SITES_TO_LOGIN

    print(f"Abrindo Chrome com perfil dedicado...")
    proc = subprocess.Popen(args)
    print(f"Chrome aberto (PID {proc.pid}).")
    print()
    print("Faça login nos sites e feche o Chrome quando terminar.")
    print("Aguardando Chrome fechar...")

    proc.wait()
    print()
    print("[OK] Chrome fechado. Perfil de pipeline configurado com sucesso.")
    print(f"     Localizacao: {PIPELINE_PROFILE}")
    print()
    print("O pipeline usara este perfil automaticamente no proximo run.")


def status() -> None:
    if PIPELINE_PROFILE.exists():
        size_mb = sum(f.stat().st_size for f in PIPELINE_PROFILE.rglob("*") if f.is_file()) / 1e6
        print(f"[OK] Perfil de pipeline existe: {PIPELINE_PROFILE}")
        print(f"     Tamanho: {size_mb:.1f} MB")
    else:
        print(f"[MISSING] Perfil de pipeline nao encontrado: {PIPELINE_PROFILE}")
        print("          Execute: python -m tools.setup_pipeline_browser")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Configura o perfil Chrome dedicado ao pipeline Clipping."
    )
    parser.add_argument(
        "--reset", action="store_true",
        help="Apaga o perfil existente e recria do zero.",
    )
    parser.add_argument(
        "--status", action="store_true",
        help="Mostra se o perfil existe e seu tamanho.",
    )
    args = parser.parse_args()

    if args.status:
        status()
    else:
        setup(reset=args.reset)
