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
import sys
import time
from datetime import datetime

from pipeline import run_pipeline


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
        output = run_pipeline(
            use_llm_screener=not args.no_llm,
            enrich_text=not args.no_enrich,
            verbose=args.verbose,
            use_legacy_screener=args.legacy_screener,
        )
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

            # Espera 1 hora para não re-executar no mesmo horário
            time.sleep(3600)
        else:
            # Verifica a cada 60 segundos
            time.sleep(60)


if __name__ == "__main__":
    main()
