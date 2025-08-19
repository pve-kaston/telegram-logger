APP_NAME = logger

INSTALL_PATH = /opt/$(APP_NAME)
CONFIG_PATH = /etc/$(APP_NAME)
SYSTEMD_PATH = /etc/systemd/system

.PHONY: install start uninstall restart status

install:
	@echo ">>> Installing $(APP_NAME)..."
	mkdir -p /opt/$(APP_NAME)
	mkdir -p /etc/$(APP_NAME)

	cp -a src/. /opt/$(APP_NAME)/

	cp -a .env.example /etc/$(APP_NAME)/

	cp $(APP_NAME).service /etc/systemd/system/

start:
	# Start and enable systemd
	systemctl daemon-reload
	systemctl enable --now $(APP_NAME).service

	@echo ">>> Final!"

uninstall:
	@echo ">>> Deleting $(APP_NAME)..."
	systemctl stop $(APP_NAME).service || true
	systemctl disable $(APP_NAME).service || true
	rm -rf /opt/$(APP_NAME)
	rm -rf /etc/$(APP_NAME)/
	rm -f /etc/systemd/system/$(APP_NAME).service
	systemctl daemon-reload
	@echo ">>> Deleted!"

restart:
	systemctl restart $(APP_NAME).service

status:
	systemctl status $(APP_NAME).service
