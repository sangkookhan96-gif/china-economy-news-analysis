"""Report Exporter - Generate PDF and Excel reports."""

import io
import json
from datetime import datetime
from typing import Optional

import pandas as pd

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from src.database.models import get_connection


class ReportExporter:
    """Export news analysis reports to PDF and Excel formats."""

    def __init__(self):
        self.report_date = datetime.now().strftime("%Y-%m-%d")

    def get_report_data(self, days: int = 7, industry: str = None,
                        min_importance: float = 0.0, include_reviews: bool = True) -> pd.DataFrame:
        """Fetch data for report generation."""
        conn = get_connection()

        query = """
            SELECT
                n.id,
                n.source,
                n.original_title,
                n.translated_title,
                n.summary,
                n.importance_score,
                n.industry_category,
                n.content_type,
                n.sentiment,
                n.market_impact,
                n.keywords,
                n.original_url,
                n.published_at,
                n.collected_at,
                n.analyzed_at,
                er.expert_comment,
                er.ai_final_review,
                er.opinion_conflict,
                er.review_completed_at
            FROM news n
            LEFT JOIN expert_reviews er ON n.id = er.news_id
            WHERE n.analyzed_at IS NOT NULL
              AND n.collected_at >= datetime('now', ?)
              AND n.importance_score >= ?
        """
        params = [f'-{days} days', min_importance]

        if industry and industry != "전체":
            query += " AND n.industry_category = ?"
            params.append(industry)

        query += " ORDER BY n.importance_score DESC, n.collected_at DESC"

        df = pd.read_sql_query(query, conn, params=params)
        conn.close()

        return df

    def export_to_excel(self, df: pd.DataFrame, filename: str = None) -> io.BytesIO:
        """Export data to Excel format."""
        output = io.BytesIO()

        with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
            workbook = writer.book

            # Define formats
            header_format = workbook.add_format({
                'bold': True,
                'bg_color': '#4472C4',
                'font_color': 'white',
                'border': 1,
                'align': 'center',
                'valign': 'vcenter',
                'text_wrap': True
            })

            cell_format = workbook.add_format({
                'border': 1,
                'valign': 'top',
                'text_wrap': True
            })

            score_high = workbook.add_format({
                'border': 1,
                'bg_color': '#FF6B6B',
                'font_color': 'white',
                'align': 'center'
            })

            score_medium = workbook.add_format({
                'border': 1,
                'bg_color': '#FFA500',
                'align': 'center'
            })

            score_low = workbook.add_format({
                'border': 1,
                'bg_color': '#90EE90',
                'align': 'center'
            })

            # Prepare summary sheet
            summary_df = df[[
                'translated_title', 'source', 'importance_score',
                'industry_category', 'sentiment', 'summary', 'market_impact'
            ]].copy()

            summary_df.columns = [
                '제목', '출처', '중요도', '산업분류', '감성', '요약', '시장영향'
            ]

            summary_df.to_excel(writer, sheet_name='뉴스 요약', index=False, startrow=1)

            worksheet = writer.sheets['뉴스 요약']

            # Write header with format
            for col_num, value in enumerate(summary_df.columns):
                worksheet.write(0, col_num, value, header_format)

            # Set column widths
            worksheet.set_column('A:A', 50)  # Title
            worksheet.set_column('B:B', 15)  # Source
            worksheet.set_column('C:C', 10)  # Importance
            worksheet.set_column('D:D', 15)  # Industry
            worksheet.set_column('E:E', 10)  # Sentiment
            worksheet.set_column('F:F', 60)  # Summary
            worksheet.set_column('G:G', 40)  # Market Impact

            # Expert reviews sheet
            if 'expert_comment' in df.columns:
                review_df = df[df['expert_comment'].notna()][[
                    'translated_title', 'importance_score', 'expert_comment',
                    'ai_final_review', 'opinion_conflict'
                ]].copy()

                if not review_df.empty:
                    review_df.columns = ['제목', '중요도', '전문가 의견', 'AI 최종 리뷰', '의견 충돌']
                    review_df['의견 충돌'] = review_df['의견 충돌'].apply(
                        lambda x: '⚠️ 충돌' if x else '✅ 일치'
                    )

                    review_df.to_excel(writer, sheet_name='전문가 리뷰', index=False, startrow=1)

                    worksheet2 = writer.sheets['전문가 리뷰']
                    for col_num, value in enumerate(review_df.columns):
                        worksheet2.write(0, col_num, value, header_format)

                    worksheet2.set_column('A:A', 50)
                    worksheet2.set_column('B:B', 10)
                    worksheet2.set_column('C:C', 50)
                    worksheet2.set_column('D:D', 50)
                    worksheet2.set_column('E:E', 12)

            # Statistics sheet
            stats_data = {
                '항목': ['총 뉴스 수', '평균 중요도', '고중요도 (0.7+)', '전문가 리뷰 완료', '의견 충돌'],
                '값': [
                    len(df),
                    f"{df['importance_score'].mean():.2f}" if len(df) > 0 else "0",
                    len(df[df['importance_score'] >= 0.7]),
                    len(df[df['expert_comment'].notna()]),
                    len(df[df['opinion_conflict'] == 1])
                ]
            }
            stats_df = pd.DataFrame(stats_data)
            stats_df.to_excel(writer, sheet_name='통계', index=False, startrow=1)

            worksheet3 = writer.sheets['통계']
            for col_num, value in enumerate(stats_df.columns):
                worksheet3.write(0, col_num, value, header_format)
            worksheet3.set_column('A:A', 20)
            worksheet3.set_column('B:B', 15)

            # Industry breakdown
            if len(df) > 0:
                industry_stats = df.groupby('industry_category').agg({
                    'id': 'count',
                    'importance_score': 'mean'
                }).reset_index()
                industry_stats.columns = ['산업분류', '뉴스 수', '평균 중요도']
                industry_stats['평균 중요도'] = industry_stats['평균 중요도'].round(2)
                industry_stats = industry_stats.sort_values('뉴스 수', ascending=False)

                industry_stats.to_excel(writer, sheet_name='산업별 통계', index=False, startrow=1)

                worksheet4 = writer.sheets['산업별 통계']
                for col_num, value in enumerate(industry_stats.columns):
                    worksheet4.write(0, col_num, value, header_format)

        output.seek(0)
        return output

    def export_to_pdf(self, df: pd.DataFrame, filename: str = None) -> io.BytesIO:
        """Export data to PDF format."""
        from fpdf import FPDF

        class PDF(FPDF):
            def __init__(self):
                super().__init__()
                # Add Korean font
                font_path = "/usr/share/fonts/truetype/nanum/NanumGothic.ttf"
                alt_font_path = "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"

                try:
                    if Path(font_path).exists():
                        self.add_font('NanumGothic', '', font_path, uni=True)
                        self.korean_font = 'NanumGothic'
                    elif Path(alt_font_path).exists():
                        self.add_font('NotoSansCJK', '', alt_font_path, uni=True)
                        self.korean_font = 'NotoSansCJK'
                    else:
                        self.korean_font = None
                except:
                    self.korean_font = None

            def header(self):
                if self.korean_font:
                    self.set_font(self.korean_font, '', 16)
                else:
                    self.set_font('Helvetica', 'B', 16)
                self.cell(0, 10, 'China Economy News Analysis Report', align='C', ln=True)
                self.set_font('Helvetica', '', 10)
                self.cell(0, 5, f'Generated: {datetime.now().strftime("%Y-%m-%d %H:%M")}', align='C', ln=True)
                self.ln(5)

            def footer(self):
                self.set_y(-15)
                self.set_font('Helvetica', 'I', 8)
                self.cell(0, 10, f'Page {self.page_no()}', align='C')

        pdf = PDF()
        pdf.add_page()

        # Statistics section
        pdf.set_font('Helvetica', 'B', 14)
        pdf.cell(0, 10, 'Summary Statistics', ln=True)
        pdf.set_font('Helvetica', '', 10)

        avg_importance = df['importance_score'].mean() if len(df) > 0 else 0
        stats_text = f"""
Total News: {len(df)}
Average Importance: {avg_importance:.2f}
High Importance (0.7+): {len(df[df['importance_score'] >= 0.7])}
Expert Reviews: {len(df[df['expert_comment'].notna()])}
Opinion Conflicts: {len(df[df['opinion_conflict'] == 1])}
        """
        pdf.multi_cell(0, 5, stats_text.strip())
        pdf.ln(5)

        # Top news section
        pdf.set_font('Helvetica', 'B', 14)
        pdf.cell(0, 10, 'Top News by Importance', ln=True)

        top_news = df.head(10)
        for idx, row in top_news.iterrows():
            pdf.set_font('Helvetica', 'B', 10)

            # Importance badge
            score = row['importance_score'] or 0
            badge = f"[{score:.2f}]"

            title = row['translated_title'] or row['original_title'] or 'No Title'
            # Truncate and clean title for PDF
            title_clean = title[:80] + '...' if len(title) > 80 else title
            title_clean = title_clean.encode('latin-1', 'replace').decode('latin-1')

            pdf.cell(0, 6, f"{badge} {title_clean}", ln=True)

            pdf.set_font('Helvetica', '', 9)
            source = row.get('source', '-')
            industry = row.get('industry_category', '-')
            pdf.cell(0, 4, f"Source: {source} | Industry: {industry}", ln=True)

            if row.get('summary'):
                summary = row['summary'][:200] + '...' if len(row['summary']) > 200 else row['summary']
                summary_clean = summary.encode('latin-1', 'replace').decode('latin-1')
                pdf.set_font('Helvetica', 'I', 8)
                pdf.multi_cell(0, 4, summary_clean)

            pdf.ln(3)

        # Industry breakdown
        pdf.add_page()
        pdf.set_font('Helvetica', 'B', 14)
        pdf.cell(0, 10, 'Industry Breakdown', ln=True)

        if len(df) > 0:
            industry_stats = df.groupby('industry_category').agg({
                'id': 'count',
                'importance_score': 'mean'
            }).reset_index()
            industry_stats.columns = ['Industry', 'Count', 'Avg Importance']
            industry_stats = industry_stats.sort_values('Count', ascending=False)

            pdf.set_font('Helvetica', 'B', 10)
            pdf.cell(60, 8, 'Industry', border=1)
            pdf.cell(30, 8, 'Count', border=1, align='C')
            pdf.cell(40, 8, 'Avg Importance', border=1, align='C', ln=True)

            pdf.set_font('Helvetica', '', 10)
            for _, row in industry_stats.iterrows():
                pdf.cell(60, 6, str(row['Industry']), border=1)
                pdf.cell(30, 6, str(row['Count']), border=1, align='C')
                pdf.cell(40, 6, f"{row['Avg Importance']:.2f}", border=1, align='C', ln=True)

        output = io.BytesIO()
        pdf_content = pdf.output()
        output.write(pdf_content)
        output.seek(0)
        return output


def generate_excel_report(days: int = 7, industry: str = None,
                         min_importance: float = 0.0) -> io.BytesIO:
    """Convenience function to generate Excel report."""
    exporter = ReportExporter()
    df = exporter.get_report_data(days=days, industry=industry, min_importance=min_importance)
    return exporter.export_to_excel(df)


def generate_pdf_report(days: int = 7, industry: str = None,
                       min_importance: float = 0.0) -> io.BytesIO:
    """Convenience function to generate PDF report."""
    exporter = ReportExporter()
    df = exporter.get_report_data(days=days, industry=industry, min_importance=min_importance)
    return exporter.export_to_pdf(df)


if __name__ == "__main__":
    # Test report generation
    exporter = ReportExporter()
    df = exporter.get_report_data(days=30)
    print(f"Found {len(df)} news items for report")

    # Generate Excel
    excel_output = exporter.export_to_excel(df)
    with open("test_report.xlsx", "wb") as f:
        f.write(excel_output.read())
    print("Excel report saved to test_report.xlsx")

    # Generate PDF
    pdf_output = exporter.export_to_pdf(df)
    with open("test_report.pdf", "wb") as f:
        f.write(pdf_output.read())
    print("PDF report saved to test_report.pdf")
