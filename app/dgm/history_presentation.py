"""Pure explanations: normalization, coverage and scope are separate facts."""
import json


def normalization(summary):
    info=summary.get('normalization') or {}
    if info.get('version'):
        state={'verified':'đã kiểm chứng','limited':'có giới hạn','needs-source':'cần nguồn','original':'bản gốc'}.get(info.get('state'),'chưa đối chiếu')
        return f"v{info['version']} • {state}"
    version=summary.get('canonical_normalization_version')
    if version and summary.get('import_complete'):return f'Chuẩn ngay khi nhập v{version}'
    return 'Chuẩn cũ / cần kiểm tra'


def needs_normalization(summary):
    from .normalization import VERSION
    return not (summary.get('normalization',{}).get('version')==VERSION or
                summary.get('canonical_normalization_version') in (1,VERSION) and summary.get('import_complete'))


def coverage(summary):
    count=summary.get('errors',0)
    if count:return f'{count:,} vùng/lỗi đọc'
    if summary.get('coverage_unverified'):return 'Thiếu bằng chứng bao phủ'
    if summary.get('coverage'):return 'Không ghi nhận lỗi đọc'
    return 'Chưa ghi đủ bằng chứng'


def pair_explanation(summary):
    older,newer=summary.get('older'),summary.get('newer')
    notes=['Chênh lệch chỉ tính phần đủ bằng chứng; không lấy hiệu hai tổng khác phạm vi.']
    if older and newer and 'config' in older and 'config' in newer:
        from .snapshot_scope import differences
        notes.extend(differences(older,newer))
    if 'unverified_all_files' in summary:notes.append(f"{summary['unverified_all_files']:,} file chưa đủ bằng chứng; không gán 0 B hoặc coi là đã xóa.")
    notes.append('Đã chuẩn hóa không có nghĩa đã đọc đủ mọi file. Dung lượng app tự loại trừ được đo riêng.')
    return '\n'.join(notes)
