APP_NAME = logger

INSTALL_PATH = /opt/$(APP_NAME)
CONFIG_PATH = /etc/$(APP_NAME)
SYSTEMD_PATH = /etc/systemd/system

.PHONY: install start uninstall restart status logs

install:
	@echo ">>> Installing $(APP_NAME)..."
	@useradd --system --no-create-home --shell /usr/sbin/nologin $(APP_NAME) || true

	@apt-get update && apt-get install -y pip3 python3-pip && pip install -r requirements.txt
	
	@mkdir -p /opt/$(APP_NAME)/db && touch /opt/$(APP_NAME)/db/messages.db && touch /opt/$(APP_NAME)/db/user.session
	@mkdir -p /etc/$(APP_NAME)

	@cp -a src/. /opt/$(APP_NAME)/ && chown -R $(APP_NAME):$(APP_NAME) /opt/$(APP_NAME)
	@cp -a .env.example /etc/$(APP_NAME)/.env && chown -R $(APP_NAME):$(APP_NAME) /etc/$(APP_NAME) && chmod 600 /etc/$(APP_NAME)/.env
	@cp $(APP_NAME).service /etc/systemd/system/ && chown $(APP_NAME):$(APP_NAME) /etc/systemd/system/$(APP_NAME).service

start:
	@echo ">>> Start and enable systemd $(APP_NAME)..."
	@systemctl daemon-reload
	@systemctl enable --now $(APP_NAME).service

	@echo ">>> Final!"

uninstall:
	@echo ">>> Deleting $(APP_NAME)..."
	@systemctl stop $(APP_NAME).service || true
	@systemctl disable $(APP_NAME).service || true
	@rm -rf /opt/$(APP_NAME)
	@rm -rf /etc/$(APP_NAME)/
	@rm -f /etc/systemd/system/$(APP_NAME).service
	@systemctl daemon-reload
	@userdel logger
	@echo ">>> Deleted!"

restart:
	@systemctl restart $(APP_NAME).service

status:
	@systemctl status $(APP_NAME).service

logs:
	@journalctl -u $(APP_NAME).service -f