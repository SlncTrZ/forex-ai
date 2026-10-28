# Quy Trình Quản Lý Phiên Bản (Version Control Protocol)

Phiên bản khởi điểm: **`v0.0.1`** (bắt đầu từ 2026-10-28).

## Quy tắc tăng version (Versioning Rules)
1. **Định dạng:** SemVer `v<major>.<minor>.<patch>` (bắt đầu với chu kỳ sơ khởi `v0.0.x`).
2. **Chu kỳ tăng Patch (`+1` số cuối):**
   - **Tự động theo chu kỳ Commit:** Cứ mỗi **3 commits** hợp lệ được tạo trên nhánh `main`, số patch cuối cùng sẽ được tăng thêm 1 đơn vị (ví dụ: `v0.0.1` -> `v0.0.2`).
   - **Theo lệnh riêng:** Khi có yêu cầu cụ thể từ User ("tăng version", "phát hành bản vá", "release").
3. **Đồng bộ vị trí version:**
   Mỗi khi tăng version, bắt buộc đồng bộ đồng thời tại:
   - `src/forex_ai/__init__.py` (`__version__ = "x.y.z"`)
   - `pyproject.toml` (`version = "x.y.z"`)
   - Git tag: `v<x.y.z>` gắn vào commit tương ứng.
