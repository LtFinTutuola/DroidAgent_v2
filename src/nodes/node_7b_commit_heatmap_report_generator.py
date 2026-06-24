import os
import json
import pandas as pd
import matplotlib.pyplot as plt
import numpy as np
import xml.sax.saxutils as saxutils
from datetime import datetime
from PIL import Image as PILImage

from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Image as RLImage, PageBreak
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.lib.colors import HexColor

from src.utils import logger, audit_snapshot

def node_7b_commit_heatmap_report_generator(state):
    logger.info("=" * 60)
    logger.info("NODE 7B: Commit Heatmap Report Generator")
    logger.info("=" * 60)

    config = state.get("config", {})
    report_config = config.get("report_generation", {})

    output_folder = report_config.get("output_folder", "output")
    os.makedirs(output_folder, exist_ok=True)

    agg_path_config = report_config.get("aggregated_data_file_path", "")
    raw_path_config = report_config.get("code_mapping_file_path", "")

    suffix = "_commit_heatmap_report.pdf"

    if agg_path_config and raw_path_config and os.path.exists(agg_path_config) and os.path.exists(raw_path_config):
        agg_path = agg_path_config
        raw_path = raw_path_config
        base_name = os.path.basename(agg_path)
        prefix = base_name[:15]
        output_pdf_name = f"{prefix}{suffix}"
    else:
        agg_path = state.get("report_aggregated_file", "")
        raw_path = state.get("report_raw_file", "")
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_pdf_name = f"{timestamp}{suffix}"

    output_pdf_path = os.path.join(output_folder, output_pdf_name)

    if not agg_path or not os.path.exists(agg_path) or not raw_path or not os.path.exists(raw_path):
        logger.error("Missing required data files for report generation.")
        return state

    logger.info("Generating commit heatmap report...")
    logger.info(f"Aggregated data: {agg_path}")
    logger.info(f"Raw data: {raw_path}")

    with open(agg_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    with open(raw_path, 'r', encoding='utf-8') as f:
        raw_data = json.load(f)

    oldest_date = None
    newest_date = None
    for item in raw_data:
        commits = item.get("commits", [])
        for c in commits:
            c_date_str = c.get("commit_date")
            if c_date_str:
                try:
                    c_date = datetime.fromisoformat(c_date_str.replace("Z", "+00:00"))
                    if oldest_date is None or c_date < oldest_date:
                        oldest_date = c_date
                    if newest_date is None or c_date > newest_date:
                        newest_date = c_date
                except ValueError:
                    pass

    analyzed_pull_requests = len(data)
    timeframe_str = "N/A"
    if oldest_date and newest_date:
        timeframe_str = f"Dal {oldest_date.strftime('%Y-%m-%d')} al {newest_date.strftime('%Y-%m-%d')}"

    records = []
    for commit in data:
        impact = commit.get("impact_score", 0)
        if impact > 0:
            desc = commit.get("commit_description", "N/A").split('\n')[0].strip()
            if len(desc) > 50:
                desc = desc[:47] + "..."
            
            short_hash = commit.get("commit_hash", "")[:7]
            y_label = f"[{short_hash}] {desc}"
            
            records.append({
                "Y_Label": y_label,
                "ImpactScore": impact,
                "ClassesCount": len(commit.get("classes", [])),
                "Classes": commit.get("classes", []),
                "CommitDesc": commit.get("commit_description", "N/A"),
                "CommitHash": commit.get("commit_hash", "")
            })

    df = pd.DataFrame(records)
    
    if df.empty:
        logger.warning("No commits with impact > 0 found. Report generation aborted.")
        return state

    df = df.sort_values(by='ImpactScore', ascending=True) 
    ordered_commits = df['Y_Label'].tolist()
    df['Y_Label'] = pd.Categorical(df['Y_Label'], categories=ordered_commits, ordered=True)

    fig_height = max(8, len(ordered_commits) * 0.4)
    plt.figure(figsize=(14, fig_height))

    # Size based on classes count
    sizes = np.clip(df['ClassesCount'] * 100, 50, 3000)

    scatter = plt.scatter(x=df['ImpactScore'], 
                          y=df['Y_Label'], 
                          s=sizes,
                          c=df['ImpactScore'], 
                          cmap='YlOrRd', 
                          alpha=0.75, 
                          edgecolors='gray',
                          linewidth=0.8)

    plt.colorbar(scatter, label='Livello di Calore (Impact Score)')

    plt.title('Bubble Chart: Complessità Complessiva dei Commit', fontsize=16, pad=20)
    plt.xlabel('Punteggio Aggregato del Commit (Impact Score)', fontsize=12)
    plt.ylabel('Commits', fontsize=12)
    plt.grid(axis='y', linestyle='--', alpha=0.4)
    plt.grid(axis='x', linestyle='--', alpha=0.2)
    plt.xlim(-0.2, df['ImpactScore'].max() + 0.8) 
    plt.tight_layout()
    
    chart_path = os.path.join(output_folder, 'temp_commit_bubble_chart.png')
    plt.savefig(chart_path, dpi=150)
    plt.close()

    doc = SimpleDocTemplate(output_pdf_path, pagesize=A4, rightMargin=30, leftMargin=30, topMargin=30, bottomMargin=30)
    styles = getSampleStyleSheet()
    
    title_style = styles['Heading1']
    title_style.alignment = 1 
    h2_style = ParagraphStyle(name='Heading2Custom', parent=styles['Heading2'], textColor=HexColor('#2c3e50'), spaceBefore=15, spaceAfter=5)
    normal_style = styles['Normal']
    bullet_style = ParagraphStyle(name='Bullet', parent=styles['Normal'], leftIndent=20, spaceAfter=2)

    story = []

    story.append(Paragraph("Report Analisi Complessità Commit", title_style))
    story.append(Spacer(1, 0.2 * inch))
    story.append(Paragraph(f"<b>Timeframe Analizzato:</b> {timeframe_str}", normal_style))
    story.append(Paragraph(f"<b>Commit Analizzati (sopra soglia):</b> {analyzed_pull_requests}", normal_style))
    updated_classes_threshold = report_config.get("updated_classes_threshold", -1)
    if updated_classes_threshold != -1:
        story.append(Paragraph(f"<b>Soglia Massima Classi Modificate:</b> {updated_classes_threshold}", normal_style))
    
    classes_filter = report_config.get("classes", [])
    if classes_filter:
        classes_str = ", ".join(classes_filter)
        story.append(Paragraph(f"<b>Filtro Classi Applicato:</b> {classes_str}", normal_style))
        
    story.append(Spacer(1, 0.2 * inch))
    story.append(Paragraph("Questo report include i commit con un <b>Impact Score aggregato maggiore di zero</b>.", normal_style))
    story.append(Spacer(1, 0.2 * inch))

    with PILImage.open(chart_path) as pimg:
        img_w, img_h = pimg.size
        aspect = img_h / img_w
        pdf_img_w = 530
        pdf_img_h = pdf_img_w * aspect
        if pdf_img_h > 700:
            pdf_img_h = 700
            pdf_img_w = 700 / aspect

    story.append(RLImage(chart_path, width=pdf_img_w, height=pdf_img_h))
    story.append(PageBreak())

    story.append(Paragraph("Dettaglio delle Classi per Commit", styles['Heading1']))
    story.append(Spacer(1, 0.1 * inch))

    df_sorted = df.sort_values(by='ImpactScore', ascending=False)

    for i, row in df_sorted.iterrows():
        commit_title = f"{row['Y_Label']} (Impatto Totale: {row['ImpactScore']:.2f})"
        story.append(Paragraph(saxutils.escape(commit_title), h2_style))
        
        # We also print the full description if truncated
        full_desc = row['CommitDesc'].strip()
        story.append(Paragraph(f"<i>{saxutils.escape(full_desc)}</i>", normal_style))
        story.append(Spacer(1, 0.05 * inch))
        
        classes = row['Classes']
        for cls in classes:
            score = cls.get('impact_score', 0)
            cname = cls.get('class_name', 'Unknown').split('.')[-1]
            pname = cls.get('project_name', 'Unknown')
            
            if score < 0.01:
                line = f"<b>[&lt;0.01]</b> {pname} ➔ {cname}"
            else:
                line = f"<b>[{score:.2f}]</b> {pname} ➔ {cname}"
                
            story.append(Paragraph(f"• {line}", bullet_style))
        
        story.append(Spacer(1, 0.1 * inch))

    doc.build(story)
    
    if os.path.exists(chart_path):
        os.remove(chart_path)
        
    logger.info(f"Report completed and saved as: {output_pdf_path}")
    
    audit_snapshot({"report_path": output_pdf_path, "analyzed_pull_requests": analyzed_pull_requests, "timeframe": timeframe_str}, "node_7b_commit_heatmap_report_generator", "Report Generation", config)

    return state
