"""Các role dài hạn dùng chung WorkerEngine.

`roles` chỉ quyết định vòng đời và nguồn kích hoạt; nghiệp vụ nằm trong
`app.workers.jobs`, cơ chế thực thi nằm trong `app.workers.engine`.
"""
