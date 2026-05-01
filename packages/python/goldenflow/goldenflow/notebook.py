"""Jupyter notebook integration for GoldenFlow."""
from __future__ import annotations

from goldenflow.engine.transformer import TransformResult
from goldenflow.engine.manifest import Manifest
from goldenflow.engine.profiler_bridge import DatasetProfile


def _transform_result_repr_html(self: TransformResult) -> str:
    """Rich HTML representation of TransformResult for Jupyter."""
    rows = self.df.shape[0]
    cols = self.df.shape[1]
    transforms = len(self.manifest.records)
    errors = len(self.manifest.errors)

    html = f'''<div style="font-family: monospace; padding: 10px; border: 1px solid #ddd; border-radius: 5px;">
    <h3 style="margin: 0 0 10px 0;">GoldenFlow TransformResult</h3>
    <table style="border-collapse: collapse; width: 100%;">
    <tr><td style="padding: 4px 8px; font-weight: bold;">Rows</td><td>{rows:,}</td></tr>
    <tr><td style="padding: 4px 8px; font-weight: bold;">Columns</td><td>{cols}</td></tr>
    <tr><td style="padding: 4px 8px; font-weight: bold;">Transforms Applied</td><td>{transforms}</td></tr>
    <tr><td style="padding: 4px 8px; font-weight: bold;">Errors</td><td style="color: {"red" if errors else "green"};">{errors}</td></tr>
    </table>'''

    if self.manifest.records:
        html += '''<h4 style="margin: 10px 0 5px 0;">Transforms</h4>
        <table style="border-collapse: collapse; width: 100%; font-size: 0.9em;">
        <tr style="background: #f5f5f5;">
            <th style="padding: 4px 8px; text-align: left;">Column</th>
            <th style="padding: 4px 8px; text-align: left;">Transform</th>
            <th style="padding: 4px 8px; text-align: left;">Affected</th>
        </tr>'''
        for r in self.manifest.records[:10]:
            html += f'''<tr>
                <td style="padding: 4px 8px;">{r.column}</td>
                <td style="padding: 4px 8px;">{r.transform}</td>
                <td style="padding: 4px 8px;">{r.affected_rows}/{r.total_rows}</td>
            </tr>'''
        if len(self.manifest.records) > 10:
            html += f'<tr><td colspan="3" style="padding: 4px 8px; color: #888;">... and {len(self.manifest.records) - 10} more</td></tr>'
        html += '</table>'

    # Include DataFrame preview
    html += f'<h4 style="margin: 10px 0 5px 0;">Preview</h4>{self.df.head(5).to_pandas().to_html(index=False)}'
    html += '</div>'
    return html


def _manifest_repr_html(self: Manifest) -> str:
    """Rich HTML representation of Manifest for Jupyter."""
    html = f'''<div style="font-family: monospace; padding: 10px; border: 1px solid #ddd; border-radius: 5px;">
    <h3>GoldenFlow Manifest</h3>
    <p>Source: {self.source} | Transforms: {len(self.records)} | Errors: {len(self.errors)}</p>
    <table style="border-collapse: collapse; width: 100%; font-size: 0.9em;">
    <tr style="background: #f5f5f5;">
        <th style="padding: 4px 8px; text-align: left;">Column</th>
        <th style="padding: 4px 8px; text-align: left;">Transform</th>
        <th style="padding: 4px 8px; text-align: left;">Affected</th>
        <th style="padding: 4px 8px; text-align: left;">Before</th>
        <th style="padding: 4px 8px; text-align: left;">After</th>
    </tr>'''
    for r in self.records:
        before = ", ".join(r.sample_before[:2]) if r.sample_before else ""
        after = ", ".join(r.sample_after[:2]) if r.sample_after else ""
        html += f'''<tr>
            <td style="padding: 4px 8px;">{r.column}</td>
            <td style="padding: 4px 8px;">{r.transform}</td>
            <td style="padding: 4px 8px;">{r.affected_rows}/{r.total_rows}</td>
            <td style="padding: 4px 8px; color: #c00;">{before}</td>
            <td style="padding: 4px 8px; color: #0a0;">{after}</td>
        </tr>'''
    html += '</table></div>'
    return html


def _profile_repr_html(self: DatasetProfile) -> str:
    """Rich HTML representation of DatasetProfile for Jupyter."""
    html = f'''<div style="font-family: monospace; padding: 10px; border: 1px solid #ddd; border-radius: 5px;">
    <h3>GoldenFlow Profile</h3>
    <p>{self.row_count:,} rows, {self.column_count} columns</p>
    <table style="border-collapse: collapse; width: 100%; font-size: 0.9em;">
    <tr style="background: #f5f5f5;">
        <th style="padding: 4px 8px; text-align: left;">Column</th>
        <th style="padding: 4px 8px; text-align: left;">Type</th>
        <th style="padding: 4px 8px; text-align: left;">Nulls</th>
        <th style="padding: 4px 8px; text-align: left;">Unique</th>
        <th style="padding: 4px 8px; text-align: left;">Sample</th>
    </tr>'''
    for c in self.columns:
        html += f'''<tr>
            <td style="padding: 4px 8px;">{c.name}</td>
            <td style="padding: 4px 8px;">{c.inferred_type}</td>
            <td style="padding: 4px 8px;">{c.null_count} ({c.null_pct:.0%})</td>
            <td style="padding: 4px 8px;">{c.unique_count}</td>
            <td style="padding: 4px 8px; color: #888;">{", ".join(c.sample_values[:3])}</td>
        </tr>'''
    html += '</table></div>'
    return html


# Monkey-patch the classes to add _repr_html_
TransformResult._repr_html_ = _transform_result_repr_html
Manifest._repr_html_ = _manifest_repr_html
DatasetProfile._repr_html_ = _profile_repr_html
