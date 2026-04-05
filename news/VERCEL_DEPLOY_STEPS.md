# Med IT Daily Feishu 上云部署指南（Vercel）

适用对象：没有开发背景，按步骤操作即可。  
目标：让日报任务每天自动执行，不依赖本地电脑开机。

## 0. 你会得到什么

部署完成后，Vercel 会每天定时访问这个地址并触发日报：

- `https://你的域名.vercel.app/api/digest_cron`

这个接口会自动执行当前脚本并发送飞书消息。

## 1. 准备账号

1. 注册/登录 [Vercel](https://vercel.com/)。
2. 注册/登录 [GitHub](https://github.com/)（Vercel 推荐从 GitHub 导入项目）。

## 2. 把当前目录放到 GitHub（网页方式）

1. 在 GitHub 新建一个仓库，例如 `med-it-daily-feishu`。
2. 在本机打开 `E:/codex/news`。
3. 把这个目录上传到刚创建的 GitHub 仓库。

如果你会命令行，也可以用标准 git 提交；不会也没关系，网页上传同样可行。

## 3. 在 Vercel 导入项目

1. 进入 Vercel，点击 `Add New...` -> `Project`。
2. 选择刚才的 GitHub 仓库并导入。
3. Framework Preset 选 `Other`（不要紧张，默认即可）。
4. 点击 `Deploy`。

## 4. 配置环境变量（最关键）

部署完成后，进入项目：

1. `Settings` -> `Environment Variables`，新增以下变量：
2. `FEISHU_WEBHOOK_URL` = 你的飞书机器人 webhook 地址
3. `FEISHU_BOT_SECRET` = 你的飞书签名密钥（如果机器人开启签名）
4. `CRON_SECRET` = 你自定义的一段随机字符串（例如 32 位）
5. 可选：`DIGEST_LOOKBACK_HOURS` = `72`
6. 可选：`DIGEST_TIMEZONE` = `Asia/Shanghai`

添加后，点击 `Redeploy` 让变量生效。

## 5. 验证接口可用

你可以在浏览器访问（手动测试）：

- `https://你的域名.vercel.app/api/digest_cron?token=你的CRON_SECRET`

看到 JSON 返回里 `ok: true`，说明任务执行成功。  
如果 `ok: false`，看返回中的 `stderr` 和 `error` 文本排查。

## 6. 确认定时任务

本项目已经带好 `vercel.json`：

- 每天 `01:00 UTC` 触发一次（北京时间约 `09:00`，实际可能有几十分钟浮动）

说明：

- Vercel Hobby 免费版限制为每天最多一次 cron。
- 如果你以后要每小时执行，需要升级到 Pro。

## 7. 你最关心的结果

完成以上步骤后：

1. 电脑关机也会自动执行。
2. 每天自动发送飞书日报。
3. 长链接固定为 `https://你的域名.vercel.app/api/digest_cron`。

## 8. 常见报错快速判断

1. `Unauthorized`：`CRON_SECRET` 不一致，检查 URL token 或定时头鉴权。
2. `FEISHU_WEBHOOK_URL not found`：环境变量没配或拼写错误。
3. 飞书返回非 `code=0`：机器人权限/签名配置不一致。
4. 没有推送消息：去 Vercel 项目 `Functions` 日志看该接口的运行日志。
