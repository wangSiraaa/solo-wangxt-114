.PHONY: test test-core test-api run load build-frontend

# 全部验收测试：核心引擎（pytest）+ 后端 API（Django test）
test: test-core test-api

test-core:
	python3 -m pytest tests/ -q

test-api:
	cd backend && python3 manage.py test forest

# 开发环境（SQLite + GeoJSON 边界；生产用 config.settings + PostGIS）
load:
	cd backend && python3 manage.py migrate && python3 manage.py load_fictional

run:
	cd backend && python3 manage.py runserver

build-frontend:
	cd frontend && npm install && npm run build
