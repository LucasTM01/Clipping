"""
Ponto de entrada do pipeline de clipping para Equity Research.

Uso:
    python main.py                       # Execução padrão (screener v2: BGE-M3 + Qdrant)
    python main.py --legacy-screener     # Screener v1 (keyword + Ollama)
    python main.py --no-llm              # Sem Ollama (só keywords, apenas com --legacy-screener)
    python main.py --no-enrich           # Sem extração de texto completo
    python main.py --verbose             # Log detalhado
    python main.py --schedule            # Roda em loop nos dias úteis
"""

import argparse
import os
import subprocess
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime

from pipeline import run_pipeline


# ---------------------------------------------------------------------------
# Docker + Qdrant startup
# ---------------------------------------------------------------------------

def _qdrant_healthy(timeout: float = 2.0) -> bool:
    """Retorna True se o Qdrant já está respondendo na porta 6333."""
    try:
        urllib.request.urlopen("http://localhost:6333/healthz", timeout=timeout)
        return True
    except Exception:
        return False


def _docker_daemon_running() -> bool:
    """Retorna True se o daemon do Docker está ativo."""
    try:
        result = subprocess.run(
            ["docker", "info"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
        return result.returncode == 0
    except Exception:
        return False


def _find_docker_desktop() -> str | None:
    """Localiza o executável do Docker Desktop no Windows."""
    candidates = [
        os.path.join(os.environ.get("PROGRAMFILES", "C:\\Program Files"),
                     "Docker", "Docker", "Docker Desktop.exe"),
        os.path.join(os.environ.get("LOCALAPPDATA", ""),
                     "Docker", "Docker Desktop.exe"),
    ]
    for path in candidates:
        if os.path.exists(path):
            return path
    return None


def ensure_qdrant(timeout_docker: int = 90, timeout_qdrant: int = 45) -> bool:
    """
    Garante que o Qdrant está rodando antes do pipeline.

    Retorna True se fomos nós que subimos o container (deve ser parado ao final).
    Retorna False se já estava rodando (não interferir).

    Passos:
    1. Se Qdrant já responde → OK, retorna False imediatamente.
    2. Tenta `docker compose up -d` (Docker já pode estar rodando).
    3. Se Docker não está rodando → inicia Docker Desktop, aguarda daemon.
    4. Aguarda Qdrant responder no health check.
    """
    # Passo 1 — já está rodando?
    if _qdrant_healthy():
        print("[Qdrant] Já está rodando ✓")
        return False  # não fomos nós que subimos

    # Passo 2 — tenta subir com docker compose (Docker pode já estar ativo)
    print("[Docker] Subindo Qdrant via docker compose...")
    result = subprocess.run(
        ["docker", "compose", "up", "-d"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    if result.returncode != 0:
        # Passo 3 — Docker daemon não está rodando, iniciar Docker Desktop
        print("[Docker] Daemon não está rodando — iniciando Docker Desktop...")
        docker_desktop = _find_docker_desktop()
        if not docker_desktop:
            print(
                "[ERRO] Docker Desktop não encontrado. Instale em https://docs.docker.com/desktop/windows/\n"
                "       Ou suba o Qdrant manualmente: docker compose up -d"
            )
            sys.exit(1)

        subprocess.Popen([docker_desktop])

        # Aguarda daemon ficar pronto
        elapsed = 0
        while elapsed < timeout_docker:
            if _docker_daemon_running():
                print(f"[Docker] Daemon pronto ✓ ({elapsed}s) — subindo Qdrant...")
                break
            time.sleep(3)
            elapsed += 3
            if elapsed % 15 == 0:
                print(f"[Docker] Aguardando daemon... ({elapsed}/{timeout_docker}s)")
        else:
            print(f"[ERRO] Docker daemon não ficou pronto em {timeout_docker}s. Verifique o Docker Desktop.")
            sys.exit(1)

        # Agora sobe o Qdrant
        subprocess.run(
            ["docker", "compose", "up", "-d"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    # Passo 4 — aguarda Qdrant responder
    elapsed = 0
    while elapsed < timeout_qdrant:
        if _qdrant_healthy():
            print(f"[Qdrant] Pronto ✓ ({elapsed}s)")
            return True  # nós subimos — caller deve parar ao final
        time.sleep(2)
        elapsed += 2
        if elapsed % 10 == 0:
            print(f"[Qdrant] Aguardando health check... ({elapsed}/{timeout_qdrant}s)")

    print(f"[ERRO] Qdrant não respondeu em {timeout_qdrant}s. Verifique: docker compose logs")
    sys.exit(1)


def shutdown_qdrant() -> None:
    """Para o container do Qdrant sem remover dados."""
    print("[Qdrant] Encerrando container...")
    subprocess.run(
        ["docker", "compose", "stop"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    print("[Qdrant] Container parado ✓")


def is_business_day() -> bool:
    """Verifica se hoje é dia útil (seg-sex)."""
    return datetime.now().weekday() < 5


def main():
    parser = argparse.ArgumentParser(
        description="Pipeline de Clipping — Equity Research (Capital Goods & Industrials)"
    )
    parser.add_argument(
        "--no-llm",
        action="store_true",
        help="Desabilita triagem via Ollama (usa apenas keywords)",
    )
    parser.add_argument(
        "--no-enrich",
        action="store_true",
        help="Desabilita extração de texto completo via trafilatura",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Ativa logs detalhados (DEBUG)",
    )
    parser.add_argument(
        "--schedule",
        action="store_true",
        help="Roda em loop, executando às 7h nos dias úteis",
    )
    parser.add_argument(
        "--schedule-hour",
        type=int,
        default=7,
        help="Hora de execução no modo schedule (default: 7)",
    )
    parser.add_argument(
        "--legacy-screener",
        action="store_true",
        help="Usar screener v1 (keyword + Ollama) em vez do v2 (BGE-M3 + Qdrant)",
    )

    args = parser.parse_args()

    if args.schedule:
        print(f"Modo schedule ativado. Execução diária às {args.schedule_hour}:00 (dias úteis).")
        _run_scheduled(args)
    else:
        we_started_qdrant = ensure_qdrant() if not args.legacy_screener else False
        try:
            output = run_pipeline(
                use_llm_screener=not args.no_llm,
                enrich_text=not args.no_enrich,
                verbose=args.verbose,
                use_legacy_screener=args.legacy_screener,
            )
        finally:
            if we_started_qdrant:
                shutdown_qdrant()
        if output:
            print(f"\nBriefing gerado: {output}")
        else:
            print("\nNenhum documento gerado. Verifique os logs.")
            sys.exit(1)


def _run_scheduled(args):
    """Loop de execução agendada."""
    target_hour = args.schedule_hour

    while True:
        now = datetime.now()

        if is_business_day() and now.hour == target_hour and now.minute < 5:
            print(f"\n[{now.strftime('%d/%m/%Y %H:%M')}] Iniciando execução agendada...")
            we_started_qdrant = ensure_qdrant() if not args.legacy_screener else False
            try:
                output = run_pipeline(
                    use_llm_screener=not args.no_llm,
                    enrich_text=not args.no_enrich,
                    verbose=args.verbose,
                    use_legacy_screener=args.legacy_screener,
                )
                if output:
                    print(f"Briefing gerado: {output}")
            except Exception as e:
                print(f"Erro na execução: {e}")
            finally:
                if we_started_qdrant:
                    shutdown_qdrant()

            # Espera 1 hora para não re-executar no mesmo horário
            time.sleep(3600)
        else:
            # Verifica a cada 60 segundos
            time.sleep(60)


if __name__ == "__main__":
    main()
