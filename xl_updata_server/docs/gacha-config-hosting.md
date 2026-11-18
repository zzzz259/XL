# 公共配置 HTTPS 安装与运维

[客户端指南](../../docs/gacha-user-guide.md) · [配置协议](../../docs/gacha-architecture.md) · [更新器安装](配置与运维.md) · [部署/保留/恢复](../../xl_deploy/README.md)

现有更新器从五张 Lua 表静态生成非个人配置，不执行 Lua。版本 staging 成功后才镜像到 data/gacha_config；失败保留上次公开版本且角色处理继续。公开目录0755、JSON0644；私有锁、账号、密钥不放宽。

启用 `[gacha_visual_assets].enabled` 后，更新器会用 0.4.1、0.4.2 共用的内置素材清单和已发布映射计算新增 ID。按同版 Lua 引用读取 Arts 的 BurstHead，或 Other 的 FairyGUI 描述及实际用到的图集页。成品以 SHA-256 命名，按图片、视觉清单、卡池目录、`current.json` 的顺序发布。图片候选失败时可沿用上一份通过验证的视觉清单。覆盖安装及客户端差分规则见[视觉素材更新协议](../../docs/gacha-visual-assets.md)。

公网 HTTPS 入口是 8.134.186.234，Certbot 安装在 `/opt/xl-gacha-certbot`。80 端口用于 HTTP-01，443 端口提供只读分发。正式 `/gacha-config/` 读取本机 `/home/admin/xl_updata_server/data/gacha_config/`。测试 `/gacha-config-test/` 走独立反向隧道，由公网主机的 `127.0.0.1:18780` 转到 test 主机的 `127.0.0.1:18080`，读取 test 实例的 `data/gacha_config/`。两个入口也各自提供视觉清单和内容寻址图片。

基础TLS已由运营者验证。正式静态目录已用与生产游戏版本一致的已验证公共配置初始化，客户端默认源为 https://8.134.186.234/gacha-config/current.json；测试入口仅供测试人员，不预置给普通用户。初始化公共JSON不等同于main代码晋级：生产updater后续持续自动导出仍需要对应pipeline代码晋级main。本文不是证书有效期凭证，现场命令确认当前状态。

## 新服务器准备

先按更新器文档安装独立Python环境、Java/Lua工具和Node renderer，按部署运维隔离分支。新服务器的云安全组和主机防火墙允许入站TCP80/443；本台已验证可达，无需重复修改。80不可达先处理控制台规则，不忽略证书验证。

以下在管理员明确选择的新服务器执行，包管理器按系统调整；不要在既有站点盲目覆盖nginx.conf：

```sh
sudo dnf install nginx python3.11 python3.11-pip acl
sudo python3.11 -m venv /opt/xl-gacha-certbot
sudo /opt/xl-gacha-certbot/bin/python -m pip install 'certbot>=5.4,<6'
sudo install -d -m 0755 /var/lib/xl-gacha-acme
sudo install -m 0644 deploy/https/nginx-bootstrap.conf /etc/nginx/nginx.conf
sudo nginx -t
sudo systemctl enable --now nginx
```

模板是完整nginx.conf；已有其他站点时先备份并合并server块。bootstrap只开放ACME webroot，其他404。使用HTTP-01为公网IP申请短期证书：

```sh
sudo /opt/xl-gacha-certbot/bin/certbot certonly --webroot -w /var/lib/xl-gacha-acme --preferred-profile shortlived --ip-address 8.134.186.234 --cert-name xl-gacha-ip --agree-tos --register-unsafely-without-email
sudo install -m 0644 deploy/https/nginx-https.conf /etc/nginx/nginx.conf
sudo nginx -t
sudo systemctl reload nginx
```

公网入口使用 `xl-gacha-ip` 证书。私钥由 root 保存在 `/etc/letsencrypt`。test 主机单独加载 [test 站点配置](../deploy/https/nginx-test-gacha.conf)，用 `nginx -t` 验证后 reload。测试隧道和内部站点均监听本机回环。Nginx 正则中的花括号量词需要引号。入口对 `current.json`、规范的卡池和视觉清单名，以及三组 SHA-256 图片路径开放 GET/HEAD。图片和版本文件使用不可变缓存，`current.json` 需要重新验证。

## 最小权限与配置引导

Nginx worker使用nginx用户，不能改成admin。若/home/admin是0700，只授予命名ACL的search：

```sh
sudo setfacl -m u:nginx:--x /home/admin
namei -l /home/admin/xl_updata_server-test/data/gacha_config/current.json
sudo -u nginx test -r /home/admin/xl_updata_server-test/data/gacha_config/current.json
```

每个父目录都需要可穿越；必要时单独加u:nginx:--x，不递归chmod整个home/data，也不赋予DAC_READ_SEARCH。公开gacha_config目录0755，JSON0644由publisher设置；其他私有文件保持私有。

正常生产由更新流程发布。离线补发需要全部五张解码表、正确版本和项目Python；CLI不会下载或部署：

```sh
.venv/bin/python -m server_app.gacha_catalog --lua-root /private/decoded-lua --version 134359843258232660 --output /home/admin/xl_updata_server-test/data/gacha_config
```

获得部署授权后才执行。引导test可将已验证的非个人JSON交给publish_catalog，不必触发完整CDN抓取，正式目录保持原状。不可变版本文件先写，current.json后切；同版本不同内容/降级拒绝。崩溃留下.publish.lock时先确认没有发布进程再删除锁重试，不改写已公开版本。

## 自动续期与状态

```sh
sudo install -m 0644 deploy/https/xl-gacha-cert-renew.service /etc/systemd/system/
sudo install -m 0644 deploy/https/xl-gacha-cert-renew.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now xl-gacha-cert-renew.timer
sudo /opt/xl-gacha-certbot/bin/certbot renew --cert-name xl-gacha-ip --dry-run --no-random-sleep-on-renew --run-deploy-hooks --deploy-hook '/usr/sbin/nginx -t && /usr/bin/systemctl reload nginx'
```

timer每6小时检查，加15分钟jitter。Certbot内部仍可能随机等待，人工dry-run使用无随机等待参数。生产只选择xl-gacha-ip，避免混入staging lineage。hook先nginx-t再reload；续期失败旧证书暂时可用，必须在过期前恢复，不关闭校验。

```sh
systemctl list-timers xl-gacha-cert-renew.timer
systemctl status xl-gacha-cert-renew.service nginx --no-pager
journalctl -u xl-gacha-cert-renew.service -n 100 --no-pager
sudo /opt/xl-gacha-certbot/bin/certbot certificates --cert-name xl-gacha-ip
openssl s_client -connect 8.134.186.234:443 -verify_ip 8.134.186.234 -verify_return_error </dev/null
```

## 验收、备份、恢复与保留

HTTPS读取manifest和file，核对SHA256/size，检查Python和Android更新/离线恢复。测试.publish.lock、/data/、/api/v1/status及穿越路径不能读取；目录不列举且不可写。正式路径不能引用test配置。配置请求不需要JWT、UID或记录。

变更前备份nginx.conf、两份renewal unit、ACL输出、整个/etc/letsencrypt（account/renewal/archive/live链接关系）。备份加密限权，不能公开。恢复后先nginx-t，恢复timer、dry-run并检查实际证书。公开配置备份包含manifest引用的版本文件；恢复先版本文件再manifest，不能只复制current。损坏时保留客户端缓存，不发布空catalog伪装成功。

发布版本保留由[xl_deploy](../../xl_deploy/README.md)的锁/current/live/journal/rollback保护与dry-run负责，不在Nginx任务里删除release。部署控制器使用独立稳定control-plane目录与control-runtime Python环境，避免删除旧release解释器后poller失效；控制器升级先停timer、持部署锁、备份目录及unit override，再切换并验活，不移动main指针或重启共享Gateway。

公共旧catalog和客户端blob目前没有自动容量回收；不要混同release保留策略。故障先检查证书/timer/目录权限/updater warning，按部署运维恢复已知健康版本。配置只更新JSON，程序逻辑仍需EXE/APK二进制升级；完整Android短信/TapTap查询仍待真机验证。
