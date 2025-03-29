install:
	uv tool install --reinstall .

package:
	uv build

release:
	uvx twine upload --repository pypi dist/*
