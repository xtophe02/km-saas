"""PDF report generation using ReportLab."""
from datetime import datetime, date
from typing import List, Dict, Union
import logging
import os
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.platypus import (
    SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image, Flowable
)
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_RIGHT, TA_LEFT
from io import BytesIO
import urllib.parse

LOGO_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "manage.png")

logger = logging.getLogger(__name__)

HEADER_PURPLE = colors.HexColor("#4E3BC2")
LINK_BLUE = colors.HexColor("#1976D2")
PAGE_BG = colors.HexColor("#EFEFF4")
WHITE = colors.white
BLACK = colors.black
BORDER_GRAY = colors.HexColor("#DDDDDD")


def draw_page_background(canvas_obj, doc):
    canvas_obj.saveState()
    canvas_obj.setFillColor(PAGE_BG)
    canvas_obj.rect(0, 0, A4[0], A4[1], fill=True, stroke=False)
    canvas_obj.restoreState()


class CheckboxFlowable(Flowable):
    def __init__(self, size=12, checked=True):
        Flowable.__init__(self)
        self.size = size
        self.checked = checked
        self.width = size
        self.height = size

    def draw(self):
        green = colors.HexColor("#4CAF50")
        self.canv.setStrokeColor(green)
        self.canv.setLineWidth(1)
        self.canv.rect(0, 0, self.size, self.size, stroke=1, fill=0)
        if self.checked:
            self.canv.setStrokeColor(green)
            self.canv.setLineWidth(1.5)
            s = self.size
            self.canv.line(s * 0.2, s * 0.5, s * 0.4, s * 0.25)
            self.canv.line(s * 0.4, s * 0.25, s * 0.8, s * 0.75)


class KMReportGenerator:
    def generate_report(self, consultant_name: str, report_date: Union[datetime, date],
                        trips: List[Dict]) -> bytes:
        try:
            buffer = BytesIO()
            doc = SimpleDocTemplate(
                buffer, pagesize=A4,
                rightMargin=15 * mm, leftMargin=15 * mm,
                topMargin=10 * mm, bottomMargin=15 * mm,
            )

            styles = getSampleStyleSheet()

            title_style = ParagraphStyle(
                "CustomTitle", parent=styles["Heading1"],
                fontSize=16, fontName="Helvetica", textColor=BLACK,
                spaceAfter=2, leading=19, leftIndent=0, alignment=TA_LEFT,
            )
            summary_style = ParagraphStyle(
                "SummaryStyle", parent=styles["Normal"],
                fontSize=10, textColor=BLACK, spaceBefore=4, spaceAfter=6,
                leftIndent=0, alignment=TA_LEFT,
            )
            link_style = ParagraphStyle(
                "LinkStyle", parent=styles["Normal"],
                fontSize=8, textColor=LINK_BLUE, leading=11,
            )
            header_style = ParagraphStyle(
                "HeaderStyle", parent=styles["Normal"],
                fontSize=7, fontName="Helvetica-Bold", textColor=WHITE,
            )
            cell_style = ParagraphStyle(
                "CellStyle", parent=styles["Normal"], fontSize=8, leading=11,
            )
            date_style = ParagraphStyle(
                "DateStyle", parent=cell_style, fontName="Helvetica-Bold",
            )
            distance_style = ParagraphStyle(
                "DistanceStyle", parent=cell_style, alignment=TA_RIGHT,
            )
            total_style = ParagraphStyle(
                "TotalStyle", parent=styles["Normal"],
                fontSize=10, fontName="Helvetica-Bold", alignment=TA_RIGHT,
            )

            formatted_date = report_date.strftime("%m/%Y")
            content = []

            # Header
            title_table_data = [[
                Paragraph(f"{consultant_name} - Mileage summary {formatted_date}", title_style),
                self._create_logo(),
            ]]
            title_table = Table(title_table_data, colWidths=[420, 90])
            title_table.setStyle(TableStyle([
                ("VALIGN", (0, 0), (0, 0), "TOP"),
                ("VALIGN", (1, 0), (1, 0), "TOP"),
                ("ALIGN", (0, 0), (0, 0), "LEFT"),
                ("ALIGN", (1, 0), (1, 0), "RIGHT"),
                ("LEFTPADDING", (0, 0), (0, 0), 0),
                ("RIGHTPADDING", (1, 0), (1, 0), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
            ]))
            content.append(title_table)
            content.append(Spacer(1, 4))

            # Declaration
            declaration_style = ParagraphStyle(
                "DeclarationStyle", parent=styles["Normal"],
                fontName="Helvetica-Oblique", fontSize=9,
                textColor=colors.HexColor("#333333"), leading=13,
                leftIndent=0, alignment=TA_LEFT,
            )
            declaration_text = """<i>By submitting this document, I declare that the information herein is correct. To ensure their compliance, they can be further inspected until the 31st December of the following year. If it turns out that data are inaccurate, the related expense could be rejected and transformed into a salary bonus.</i>"""
            checkbox = CheckboxFlowable(size=9, checked=True)
            declaration_table = Table(
                [[checkbox, Paragraph(declaration_text, declaration_style)]],
                colWidths=[14, None],
            )
            declaration_table.setStyle(TableStyle([
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("TOPPADDING", (0, 0), (0, 0), 2),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
            ]))
            content.append(declaration_table)
            content.append(Spacer(1, 4))

            # Summary
            total_km = sum(trip["total_distance"] for trip in trips)
            encoded_days = len(trips)
            content.append(Paragraph(
                f"Quick summary : <b>{encoded_days}</b> encoded days for a total of <b>{total_km:,.2f}</b> km.",
                summary_style,
            ))
            content.append(Spacer(1, 6))

            # Table
            table_data = [[
                Paragraph("Day", header_style),
                Paragraph("Travel name", header_style),
                Paragraph("From", header_style),
                Paragraph("To", header_style),
                Paragraph("Distance", header_style),
            ]]

            for trip in trips:
                trip_date = datetime.strptime(trip["date"], "%Y-%m-%d").strftime("%d/%m/%Y")
                for i, site in enumerate(trip["sites"]):
                    start = trip["start_address"] if i == 0 else trip["sites"][i - 1]["address"]
                    end = site["address"]
                    date_cell = Paragraph(trip_date, date_style) if i == 0 else Paragraph("", cell_style)
                    table_data.append([
                        date_cell,
                        Paragraph("<u>work</u>", link_style),
                        Paragraph(self._format_address_link(start), link_style),
                        Paragraph(self._format_address_link(end), link_style),
                        Paragraph(f"{site['distance']:,.2f} Km", distance_style),
                    ])

                if trip["return_distance"] > 0:
                    last_site = trip["sites"][-1]["address"]
                    table_data.append([
                        Paragraph("", cell_style),
                        Paragraph("<u>work</u>", link_style),
                        Paragraph(self._format_address_link(last_site), link_style),
                        Paragraph(self._format_address_link(trip["start_address"]), link_style),
                        Paragraph(f"{trip['return_distance']:,.2f} Km", distance_style),
                    ])

            table_style_commands = [
                ("BACKGROUND", (0, 0), (-1, 0), HEADER_PURPLE),
                ("TEXTCOLOR", (0, 0), (-1, 0), WHITE),
                ("ALIGN", (0, 0), (-1, 0), "LEFT"),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, 0), 7),
                ("BOTTOMPADDING", (0, 0), (-1, 0), 2),
                ("TOPPADDING", (0, 0), (-1, 0), 2),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 2),
                ("RIGHTPADDING", (0, 0), (-1, -1), 2),
                ("TOPPADDING", (0, 1), (-1, -1), 1),
                ("BOTTOMPADDING", (0, 1), (-1, -1), 1),
                ("ALIGN", (-1, 0), (-1, -1), "RIGHT"),
                ("LINEBELOW", (0, 0), (-1, -1), 0.5, BORDER_GRAY),
                ("LINEBEFORE", (1, 0), (-1, -1), 0.5, BORDER_GRAY),
                ("BOX", (0, 0), (-1, -1), 0.5, BORDER_GRAY),
                ("BACKGROUND", (0, 1), (-1, -1), PAGE_BG),
            ]

            table = Table(table_data, colWidths=[65, 70, 150, 150, 75], repeatRows=1)
            table.setStyle(TableStyle(table_style_commands))
            content.append(table)

            content.append(Spacer(1, 4))
            content.append(Paragraph(f"<b>Total : {total_km:,.2f} Km</b>", total_style))

            doc.build(content, onFirstPage=draw_page_background, onLaterPages=draw_page_background)
            pdf = buffer.getvalue()
            buffer.close()
            return pdf

        except Exception as e:
            logger.error(f"Error generating PDF: {e}")
            raise ValueError(f"Failed to generate PDF: {str(e)}")

    def _create_logo(self):
        try:
            if os.path.exists(LOGO_PATH):
                return Image(LOGO_PATH, width=60, height=18)
        except Exception:
            pass
        logo_style = ParagraphStyle(
            "LogoStyle", fontSize=14, fontName="Helvetica-Bold",
            textColor=BLACK, alignment=TA_RIGHT,
        )
        return Paragraph(
            '<font color="#7C4DFF"><b>|</b></font><font color="#448AFF"><b>|</b></font> <b>Manage</b>',
            logo_style,
        )

    def _format_address_link(self, address: str) -> str:
        encoded = urllib.parse.quote(address)
        return f'<link href="https://maps.google.com/?q={encoded}"><u>{address}</u></link>'
