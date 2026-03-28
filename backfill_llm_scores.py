"""
Backfill LLM scores for existing papers that don't have scores yet
"""
import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

from app import create_app, db
from models import Paper
from scheduler import _get_llm_evaluator
from sqlalchemy import text

def backfill_scores():
    """Backfill LLM scores for papers without scores"""
    app = create_app()
    with app.app_context():
        # Get papers without LLM scores
        papers = db.session.query(Paper).filter(Paper.llm_score.is_(None)).limit(50).all()

        if not papers:
            print("[OK] All papers already have LLM scores")
            return

        print(f"Found {len(papers)} papers without LLM scores")
        print(f"Processing first 50 papers...")

        # Get LLM evaluator
        try:
            evaluator = _get_llm_evaluator()
            if not evaluator:
                print("[WARN] LLM evaluator not enabled, skipping...")
                return
        except Exception as e:
            print(f"[ERROR] Failed to initialize LLM evaluator: {e}")
            return

        success_count = 0
        for paper in papers:
            try:
                # Evaluate paper
                result = evaluator.evaluate(
                    title=paper.title,
                    abstract=paper.abstract or ''
                )

                if result.error:
                    print(f"[SKIP] Paper {paper.id}: {result.error}")
                    continue

                # Update paper score
                paper.llm_score = result.score
                print(f"[OK] Paper {paper.id}: score={result.score}")
                success_count += 1

            except Exception as e:
                print(f"[ERROR] Paper {paper.id}: {e}")
                continue

        # Commit changes
        try:
            db.session.commit()
            print(f"\n[OK] Successfully updated {success_count} papers")
        except Exception as e:
            print(f"\n[ERROR] Failed to commit: {e}")
            db.session.rollback()

if __name__ == '__main__':
    backfill_scores()
