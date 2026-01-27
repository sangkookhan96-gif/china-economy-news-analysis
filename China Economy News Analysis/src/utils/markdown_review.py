"""Markdown-based expert review system with Git integration."""

import os
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, List
import json


class MarkdownReviewManager:
    """Manage expert reviews as Git-versioned Markdown files."""

    def __init__(self, base_path: str = None):
        if base_path is None:
            base_path = Path(__file__).resolve().parent.parent.parent / "reviews"
        self.base_path = Path(base_path)
        self.base_path.mkdir(parents=True, exist_ok=True)

    def _get_review_path(self, news_id: int, date: datetime = None) -> Path:
        """Get the path for a review file."""
        if date is None:
            date = datetime.now()
        date_folder = self.base_path / date.strftime("%Y-%m-%d")
        date_folder.mkdir(parents=True, exist_ok=True)
        return date_folder / f"news_{news_id}.md"

    def _git_commit(self, file_path: Path, message: str) -> bool:
        """Auto commit the file to git."""
        try:
            repo_root = self.base_path.parent
            rel_path = file_path.relative_to(repo_root)

            # Git add
            subprocess.run(
                ["git", "add", str(rel_path)],
                cwd=repo_root,
                capture_output=True,
                check=True
            )

            # Git commit
            subprocess.run(
                ["git", "commit", "-m", message],
                cwd=repo_root,
                capture_output=True,
                check=True
            )
            return True
        except subprocess.CalledProcessError:
            return False
        except Exception:
            return False

    def generate_template(self, news: Dict) -> str:
        """Generate Markdown template for a news item."""
        title = news.get('translated_title') or news.get('original_title', '제목 없음')

        template = f"""# {title}

## 뉴스 정보
| 항목 | 내용 |
|------|------|
| 출처 | {news.get('source', '-')} |
| 수집일 | {news.get('collected_at', '-')[:10] if news.get('collected_at') else '-'} |
| 중요도 | {news.get('importance_score', 0):.2f} |
| 산업분류 | {news.get('industry_category', '-')} |
| 감성 | {news.get('sentiment', '-')} |

## AI 분석 요약
{news.get('summary', '요약 없음')}

## AI 시장영향 분석
{news.get('market_impact', '분석 없음')}

## 전문가 논평
<!-- 아래에 전문가 논평을 작성하세요 -->



---

## 메타데이터
- 뉴스 ID: {news.get('id', '-')}
- 원문 URL: {news.get('original_url', '-')}
- 키워드: {news.get('keywords', '-')}
- 리뷰 작성일: {datetime.now().strftime('%Y-%m-%d %H:%M')}
"""
        return template

    def save_review(self, news_id: int, content: str, news: Dict = None,
                    auto_commit: bool = True, date: datetime = None) -> Dict:
        """Save expert review as Markdown file with optional git commit."""
        if date is None:
            date = datetime.now()

        file_path = self._get_review_path(news_id, date)

        # If it's a new file and we have news info, generate template
        if not file_path.exists() and news:
            template = self.generate_template(news)
            # Insert expert comment into template
            template = template.replace(
                "<!-- 아래에 전문가 논평을 작성하세요 -->\n\n\n",
                f"<!-- 아래에 전문가 논평을 작성하세요 -->\n\n{content}\n"
            )
            content = template

        # Write file
        file_path.write_text(content, encoding='utf-8')

        result = {
            'success': True,
            'file_path': str(file_path),
            'committed': False,
            'message': '파일이 저장되었습니다.'
        }

        # Auto commit
        if auto_commit:
            title = news.get('translated_title', f'뉴스 {news_id}')[:50] if news else f'뉴스 {news_id}'
            commit_msg = f"Add expert review: {title}"

            if self._git_commit(file_path, commit_msg):
                result['committed'] = True
                result['message'] = '파일이 저장되고 Git에 커밋되었습니다.'
            else:
                result['message'] = '파일은 저장되었으나 Git 커밋에 실패했습니다.'

        return result

    def load_review(self, news_id: int, date: datetime = None) -> Optional[str]:
        """Load existing review content."""
        if date:
            file_path = self._get_review_path(news_id, date)
            if file_path.exists():
                return file_path.read_text(encoding='utf-8')

        # Search in all date folders
        for date_folder in sorted(self.base_path.iterdir(), reverse=True):
            if date_folder.is_dir():
                file_path = date_folder / f"news_{news_id}.md"
                if file_path.exists():
                    return file_path.read_text(encoding='utf-8')

        return None

    def get_review_path(self, news_id: int) -> Optional[Path]:
        """Get the path of existing review file."""
        for date_folder in sorted(self.base_path.iterdir(), reverse=True):
            if date_folder.is_dir():
                file_path = date_folder / f"news_{news_id}.md"
                if file_path.exists():
                    return file_path
        return None

    def list_reviews(self, date: datetime = None, limit: int = 50) -> List[Dict]:
        """List all reviews, optionally filtered by date."""
        reviews = []

        folders = sorted(self.base_path.iterdir(), reverse=True)
        if date:
            folders = [self.base_path / date.strftime("%Y-%m-%d")]

        for date_folder in folders:
            if not date_folder.is_dir():
                continue

            for file_path in sorted(date_folder.glob("news_*.md"), reverse=True):
                if len(reviews) >= limit:
                    break

                try:
                    news_id = int(file_path.stem.replace("news_", ""))
                    content = file_path.read_text(encoding='utf-8')

                    # Extract title from first line
                    lines = content.split('\n')
                    title = lines[0].replace('# ', '') if lines else '제목 없음'

                    reviews.append({
                        'news_id': news_id,
                        'title': title,
                        'date': date_folder.name,
                        'file_path': str(file_path),
                        'preview': content[:200] + '...' if len(content) > 200 else content
                    })
                except (ValueError, IOError):
                    continue

            if len(reviews) >= limit:
                break

        return reviews

    def extract_expert_comment(self, content: str) -> str:
        """Extract expert comment section from Markdown content."""
        if "## 전문가 논평" not in content:
            return ""

        # Find the expert comment section
        parts = content.split("## 전문가 논평")
        if len(parts) < 2:
            return ""

        comment_section = parts[1]

        # Find the end (next section or metadata)
        end_markers = ["---", "## 메타데이터"]
        for marker in end_markers:
            if marker in comment_section:
                comment_section = comment_section.split(marker)[0]

        # Clean up
        lines = comment_section.strip().split('\n')
        # Remove the HTML comment line
        lines = [l for l in lines if not l.strip().startswith('<!--')]

        return '\n'.join(lines).strip()

    def update_expert_comment(self, content: str, new_comment: str) -> str:
        """Update the expert comment section in Markdown content."""
        if "## 전문가 논평" not in content:
            return content

        # Split at expert comment section
        parts = content.split("## 전문가 논평")
        if len(parts) < 2:
            return content

        before = parts[0]
        after_comment = parts[1]

        # Find where the comment section ends
        end_marker = "---"
        if end_marker in after_comment:
            remaining = end_marker + after_comment.split(end_marker, 1)[1]
        else:
            remaining = ""

        # Rebuild the content
        updated = f"""{before}## 전문가 논평
<!-- 아래에 전문가 논평을 작성하세요 -->

{new_comment}

{remaining}"""

        return updated


def get_review_manager() -> MarkdownReviewManager:
    """Get singleton instance of MarkdownReviewManager."""
    return MarkdownReviewManager()
