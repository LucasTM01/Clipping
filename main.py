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
import shutil
import subprocess
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from config.settings import settings
from pipeline import run_pipeline

_PROJECT_DIR = Path(__file__).resolve().parent
_COMPOSE = ["docker", "compose", "-f", str(_PROJECT_DIR / "docker-compose.yml")]


# ---------------------------------------------------------------------------
# Ollama startup
# ---------------------------------------------------------------------------

def _ollama_healthy(timeout: float = 2.0) -> bool:
    """Retorna True se o servidor Ollama responde em OLLAMA_BASE_URL."""
    try:
        urllib.request.urlopen(f"{settings.ollama_base_url.rstrip('/')}/api/version", timeout=timeout)
        return True
    except Exception:
        return False


def _find_ollama() -> str | None:
    """Localiza o executável do Ollama (PATH ou instalação padrão no Windows)."""
    found = shutil.which("ollama")
    if found:
        return found
    candidates = [
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Ollama", "ollama.exe"),
        os.path.join(os.environ.get("PROGRAMFILES", "C:\\Program Files"), "Ollama", "ollama.exe"),
    ]
    for path in candidates:
        if os.path.exists(path):
            return path
    return None


def _ollama_needed(args) -> bool:
    """O run usa Ollama? (análise + briefing v2, Estágio A do deep dive, screener legado)."""
    deep_dive = settings.deep_dive_enabled if args.deep_dive is None else args.deep_dive
    return (
        settings.analysis_backend == "ollama"
        or (deep_dive and settings.summary_backend == "ollama")
        or args.legacy_screener
    )


def ensure_ollama(timeout: int = 60) -> bool:
    """
    Garante que o servidor Ollama está rodando antes do pipeline.

    Sem ele, análise, briefing v2 e deep dive caem no fallback (aconteceu nos
    runs de 14/10 e 18/10 — tarefa S4U roda sem o app do Ollama aberto).
    Sobe ``ollama serve`` em background e deixa rodando ao final: o modelo sai
    da VRAM sozinho após 5 min ocioso, e o próximo run já o encontra de pé.
    Nunca aborta o pipeline — se não subir, o run segue com o fallback.
    Retorna True se o Ollama está disponível.
    """
    if _ollama_healthy():
        print("[Ollama] Já está rodando ✓")
        _check_ollama_model()
        return True

    host = urlparse(settings.ollama_base_url).hostname or ""
    if host not in ("localhost", "127.0.0.1", "::1"):
        print(f"[AVISO] Ollama remoto ({settings.ollama_base_url}) não responde — não dá para iniciá-lo daqui.")
        return False

    exe = _find_ollama()
    if not exe:
        print("[AVISO] Ollama não encontrado (instale em https://ollama.com). Pipeline seguirá com fallback.")
        return False

    print("[Ollama] Não está rodando — iniciando `ollama serve`...")
    env = os.environ.copy()
    parsed = urlparse(settings.ollama_base_url)
    env.setdefault("OLLAMA_HOST", f"{parsed.hostname}:{parsed.port or 11434}")
    log_file = open(_PROJECT_DIR / "output" / "ollama_serve.log", "ab")
    flags = 0
    if sys.platform == "win32":
        flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
    try:
        subprocess.Popen(
            [exe, "serve"],
            stdin=subprocess.DEVNULL,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            env=env,
            creationflags=flags,
            close_fds=True,
        )
    except Exception as e:
        print(f"[AVISO] Falha ao iniciar o Ollama ({e}). Pipeline seguirá com fallback.")
        return False
    finally:
        log_file.close()

    elapsed = 0
    while elapsed < timeout:
        if _ollama_healthy():
            print(f"[Ollama] Pronto ✓ ({elapsed}s)")
            _check_ollama_model()
            return True
        time.sleep(2)
        elapsed += 2

    print(f"[AVISO] Ollama não respondeu em {timeout}s (ver output/ollama_serve.log). "
          "Pipeline seguirá com fallback.")
    return False


def _check_ollama_model() -> None:
    """Avisa se o modelo de análise não está baixado (o Ollama devolveria 404)."""
    import json
    try:
        with urllib.request.urlopen(f"{settings.ollama_base_url.rstrip('/')}/api/tags", timeout=5) as r:
            names = {m.get("name", "") for m in json.load(r).get("models", [])}
    except Exception:
        return
    model = settings.analysis_model
    if model not in names and f"{model}:latest" not in names:
        print(f"[AVISO] Modelo '{model}' não está no Ollama. Rode: ollama pull {model}")


# ---------------------------------------------------------------------------
# Docker + Qdrant startup
# ---------------------------------------------------------------------------

def _qdrant_healthy(timeout: float = 2.0) -> bool:
    """Retorna True se o Qdrant já está respondendo em QDRANT_HOST:QDRANT_PORT."""
    try:
        urllib.request.urlopen(
            f"http://{settings.qdrant_host}:{settings.qdrant_port}/healthz", timeout=timeout
        )
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


def _compose_up() -> subprocess.CompletedProcess:
    """`docker compose up -d` com o compose do projeto, independente do cwd."""
    return subprocess.run(
        [*_COMPOSE, "up", "-d"],
        cwd=_PROJECT_DIR,
        capture_output=True,
        text=True,
        timeout=180,
    )


def _start_docker_desktop() -> bool:
    """Pede ao Docker Desktop para iniciar. Retorna False se não há como."""
    # CLI oficial (Docker Desktop 4.37+): funciona mesmo sem o app na bandeja.
    try:
        r = subprocess.run(
            ["docker", "desktop", "start", "--detach"],
            capture_output=True, text=True, timeout=60,
        )
        if r.returncode == 0:
            return True
    except Exception:
        pass
    docker_desktop = _find_docker_desktop()
    if not docker_desktop:
        return False
    subprocess.Popen([docker_desktop])
    return True


def ensure_qdrant(timeout_docker: int = 180, timeout_qdrant: int = 60) -> bool:
    """
    Garante que o Qdrant está rodando antes do pipeline.

    Retorna True se fomos nós que subimos o container (deve ser parado ao final).
    Retorna False se já estava rodando — ou se não foi possível subi-lo; nesse
    caso o pipeline roda mesmo assim, falha na triagem e o email de ops sai
    com ❌ ERRO (antes o processo morria com sys.exit e nenhum email saía).

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
    try:
        result = _compose_up()
    except Exception as e:
        result = subprocess.CompletedProcess([], 1, "", str(e))

    if result.returncode != 0:
        # Passo 3 — verificar se o daemon está rodando ou se é outro problema
        if _docker_daemon_running():
            print(
                "[ERRO] docker compose up falhou mesmo com o daemon rodando:\n"
                f"       {result.stderr.strip()[:500]}"
            )
            return False

        print("[Docker] Daemon não está rodando — iniciando Docker Desktop...")
        if not _start_docker_desktop():
            print(
                "[ERRO] Docker Desktop não encontrado. Instale em https://docs.docker.com/desktop/windows/\n"
                "       Ou suba o Qdrant manualmente: docker compose up -d"
            )
            return False

        # Aguarda daemon ficar pronto (cold start do WSL2 leva 1–2 min)
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
            return False

        # Agora sobe o Qdrant
        try:
            result = _compose_up()
        except Exception as e:
            result = subprocess.CompletedProcess([], 1, "", str(e))
        if result.returncode != 0:
            print(f"[ERRO] docker compose up falhou: {result.stderr.strip()[:500]}")
            return False

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
    return True  # o container foi subido por nós — parar ao final mesmo assim


def shutdown_qdrant() -> None:
    """Para o container do Qdrant sem remover dados."""
    print("[Qdrant] Encerrando container...")
    subprocess.run(
        [*_COMPOSE, "stop"],
        cwd=_PROJECT_DIR,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=120,
    )
    print("[Qdrant] Container parado ✓")


def is_business_day() -> bool:
    """Verifica se hoje é dia útil (seg-sex)."""
    return datetime.now().weekday() < 5


def main():
    # Sem console (Task Scheduler, redirecionamento) o stdout cai em cp1252 e um
    # print com "✓" derrubaria o run antes do pipeline começar.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

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
    parser.add_argument(
        "--deep-dive",
        dest="deep_dive",
        action="store_true",
        default=None,
        help="Força o 3º email (análise detalhada em 2 estágios), ignorando o .env",
    )
    parser.add_argument(
        "--no-deep-dive",
        dest="deep_dive",
        action="store_false",
        help="Desabilita o 3º email mesmo com DEEP_DIVE_ENABLED=true",
    )
    parser.add_argument(
        "--clipping_version", "--clipping-version",
        dest="clipping_version",
        choices=["v1", "v2"],
        default="v2",
        help="Versão do email briefing: v2 (default, 'AI View' em inglês por setor) "
             "ou v1 (legado, PT com tese)",
    )

    args = parser.parse_args()

    if args.schedule:
        print(f"Modo schedule ativado. Execução diária às {args.schedule_hour}:00 (dias úteis).")
        _run_scheduled(args)
    else:
        we_started_qdrant = ensure_qdrant() if not args.legacy_screener else False
        if _ollama_needed(args):
            ensure_ollama()
        try:
            output = run_pipeline(
                use_llm_screener=not args.no_llm,
                enrich_text=not args.no_enrich,
                verbose=args.verbose,
                use_legacy_screener=args.legacy_screener,
                deep_dive=args.deep_dive,
                clipping_version=args.clipping_version,
            )
        finally:
            if we_started_qdrant:
                shutdown_qdrant()
        # Antes de verificar o documento: a base de notícias foi atualizada mesmo
        # quando nenhum briefing é gerado, e é ela que o DataHandler consome.
        _sync_to_datahandler()
        if output:
            print(f"\nBriefing gerado: {output}")
        else:
            print("\nNenhum documento gerado. Verifique os logs.")
        _hard_exit(0 if output else 1)


def _hard_exit(code: int) -> None:
    """Encerra sem a finalização do interpretador.

    Com torch/CUDA, Playwright e pools de threads carregados, a finalização do
    interpretador no Windows é frágil (os runs agendados terminavam com
    0xC0000005 — a causa principal era o sync, ver _sync_to_datahandler). Tudo já
    foi gravado aqui; basta descarregar logs e stdout para o Agendador ver o
    código de saída real.
    """
    import logging
    logging.shutdown()
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.flush()
        except Exception:
            pass
    os._exit(code)


def _sync_to_datahandler() -> None:
    """Publica a base de notícias no data lake do DataHandler (Google Drive).

    Best-effort por definição: o trabalho deste pipeline é o briefing, e um Drive
    offline, ainda sincronizando ou montado em outra letra é condição normal —
    não pode transformar uma execução boa em falha. Roda mesmo quando nenhum
    documento é gerado, porque a base de notícias foi atualizada de todo modo.

    SEMPRE em subprocesso: com o torch já carregado pelo screener, o pyarrow do
    sync derruba o processo com access violation (0xC0000005) — nenhuma exceção
    Python, o run morria ali e o lake nunca era atualizado pelos runs agendados.
    """
    print("\nDataHandler: sincronizando a base de notícias...")
    _sync_to_datahandler_subprocess()


def _sync_to_datahandler_subprocess() -> None:
    """Roda o entry point manual (``python -m data.drive_sync``) isolado do main."""
    command = [sys.executable, "-m", "data.drive_sync"]
    try:
        result = subprocess.run(
            command,
            cwd=os.path.dirname(os.path.abspath(__file__)),
            text=True,
            encoding="utf-8",
            errors="replace",
            env={**os.environ, "PYTHONUTF8": "1"},
            capture_output=True,
            timeout=600,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"DataHandler: sync falhou ({exc}).")
        return

    if result.stdout.strip():
        print(result.stdout.strip())
    if result.stderr.strip():
        print(result.stderr.strip())
    if result.returncode != 0:
        print(f"DataHandler: sync terminou com código {result.returncode}.")


def _run_scheduled(args):
    """Loop de execução agendada."""
    target_hour = args.schedule_hour

    while True:
        now = datetime.now()

        if is_business_day() and now.hour == target_hour and now.minute < 5:
            print(f"\n[{now.strftime('%d/%m/%Y %H:%M')}] Iniciando execução agendada...")
            we_started_qdrant = ensure_qdrant() if not args.legacy_screener else False
            if _ollama_needed(args):
                ensure_ollama()
            try:
                output = run_pipeline(
                    use_llm_screener=not args.no_llm,
                    enrich_text=not args.no_enrich,
                    verbose=args.verbose,
                    use_legacy_screener=args.legacy_screener,
                    deep_dive=args.deep_dive,
                    clipping_version=args.clipping_version,
                )
                if output:
                    print(f"Briefing gerado: {output}")
                _sync_to_datahandler()
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
