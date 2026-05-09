# -*- coding: utf-8 -*-
"""Deterministic generator for the NimbusFlow self-evolving agent dataset."""
import json
import os
import re

BASE = "/root/autodl-tmp/self_evolving_agent/data"
KB_DIR = os.path.join(BASE, "kb")
EVAL_DIR = os.path.join(BASE, "eval")

# ---------------------------------------------------------------------------
# 1) Knowledge base documents
#    Each entry: (topic, title, body)
#    Bodies are 150-400 chars of plausible help-center prose.
# ---------------------------------------------------------------------------
KB = [
    # ---- billing ----
    ("billing", "如何升级或降级 NimbusFlow 订阅套餐",
     "您可以随时在 NimbusFlow 中调整订阅套餐。请进入 设置>账单 页面，点击「管理套餐」，选择目标套餐（Starter、Team 或 Business）后确认。"
     "升级会立即生效，系统按比例补差价（proration）；降级则在当前计费周期结束后生效，以免重复扣费。"
     "若您按年付费，降级产生的余额会以账户积分（account credit）形式保留，用于下次续费抵扣。"
     "套餐变更不会影响已有数据与成员设置。如需企业级定制套餐，请联系销售团队获取报价。"),

    ("billing", "NimbusFlow 发票与收据下载指南",
     "所有 NimbusFlow 付费账户都可自助下载发票。请进入 设置>账单>发票历史，找到对应月份的记录，点击「下载 PDF」即可。"
     "发票包含账户名称、计费周期、金额与税费明细。如需在发票上添加公司抬头、税号（VAT/Tax ID）或采购订单号（PO number），"
     "请在 设置>账单>账单信息 中填写，保存后新生成的发票会自动包含这些信息。历史发票一旦开具无法修改抬头。"
     "电子发票通常在扣费成功后 24 小时内可下载。"),

    ("billing", "为什么我被重复扣费了",
     "重复扣费通常由以下原因造成：一是同一账户绑定了两张信用卡且均被尝试；二是上一次扣费因银行风控被拒后系统自动重试成功，造成短时间内两笔授权。"
     "请先进入 设置>账单>付款方式 检查是否存在多张有效卡片，并删除多余卡片。"
     "若确认是被重复扣款，多数情况下其中一笔为预授权（pending authorization），会在 3-5 个工作日内自动撤销，不会真正入账。"
     "若 5 个工作日后仍显示两笔实际扣款，请保留银行流水截图并提交账单工单。"),

    ("billing", "如何更换 NimbusFlow 的付款方式",
     "更新信用卡或绑定新的付款方式非常简单。请进入 设置>账单>付款方式，点击「添加付款方式」，输入新的信用卡或绑定 PayPal。"
     "添加成功后，将新方式设为「默认」，系统下次续费时会使用它。建议在旧卡过期前至少提前 7 天完成更换，避免因扣费失败导致服务暂停。"
     "若扣费连续失败，账户会进入宽限期（grace period），期间功能受限但数据保留。NimbusFlow 不会存储完整卡号，所有支付由 PCI 合规的支付网关处理。"),

    # ---- account_security ----
    ("account_security", "如何重置 NimbusFlow 登录密码",
     "如果忘记密码，请在登录页点击「忘记密码」，输入您注册时使用的邮箱。系统会发送一封密码重置邮件，"
     "邮件中的重置链接 15分钟内有效，过期需重新申请。点击链接后设置新密码，新密码至少 8 位且需包含字母与数字。"
     "若您没有收到邮件，请检查垃圾邮件文件夹，并确认输入的邮箱与账户邮箱一致。重置成功后，所有已登录设备会被强制登出，需重新登录。"
     "出于安全考虑，新密码不能与最近 3 次使用过的密码相同。"),

    ("account_security", "开启两步验证（2FA）保护账户",
     "为账户开启两步验证可大幅提升安全性。请进入 设置>安全，找到「两步验证」开关并开启。"
     "您可以选择基于验证器 App 的 TOTP（如 Google Authenticator、Authy）或短信验证码两种方式。"
     "开启后系统会生成一组一次性恢复码（recovery codes），请务必妥善离线保存——若手机丢失，这是您唯一能登录的方式。"
     "建议管理员在 设置>安全>组织策略 中为全体成员强制启用 2FA。每个恢复码只能使用一次。"),

    ("account_security", "登录时提示「账户已锁定」怎么办",
     "当连续 5 次输入错误密码时，NimbusFlow 会临时锁定账户 30 分钟，以防止暴力破解。"
     "等待 30 分钟后锁定会自动解除，您也可以通过「忘记密码」流程重置密码来立即解锁。"
     "如果您确认自己没有尝试登录，却收到锁定通知，可能有人正在尝试登录您的账户，建议立即重置密码并开启两步验证。"
     "企业版管理员可在 设置>安全>登录日志 中查看异常登录来源 IP。"),

    ("account_security", "管理已登录设备与会话",
     "您可以查看并撤销账户在各设备上的登录会话。请进入 设置>安全>活动会话，这里列出了所有当前登录的设备、浏览器与最近活动时间。"
     "如果发现陌生设备，点击其旁边的「登出」即可立即终止该会话。点击「登出所有其他设备」可一键清除除当前设备外的全部会话。"
     "更换密码后系统也会自动登出其他设备。建议定期检查活动会话，尤其是在公共电脑登录后。"),

    # ---- integrations_api ----
    ("integrations_api", "如何创建和管理 NimbusFlow API Key",
     "要通过 API 访问 NimbusFlow，您需要先创建 API Key。请进入 设置>集成>API，点击「生成新密钥」，为密钥命名并选择权限范围（scope）。"
     "密钥生成后只会完整显示一次，请立即复制并安全保存；离开页面后只能看到末尾几位。"
     "在 API 请求中通过 Authorization: Bearer <token> 请求头携带密钥。若密钥泄露，请立即在同一页面「吊销」（revoke）该密钥并重新生成。"
     "默认 API 速率限制为每分钟 600 次请求，超出会返回 429 状态码。"),

    ("integrations_api", "连接 Slack 与 NimbusFlow",
     "将 NimbusFlow 与 Slack 集成后，任务更新和提醒会自动推送到指定频道。请进入 设置>集成>应用市场，找到 Slack 并点击「连接」，"
     "在弹出的 OAuth 授权页登录您的 Slack 工作区并授权。授权完成后，回到 NimbusFlow 选择要接收通知的频道与事件类型（如任务分配、截止提醒、评论）。"
     "需要 Slack 工作区管理员权限才能完成首次安装。若通知未送达，请检查该频道是否仍存在以及机器人是否被移出。"),

    ("integrations_api", "设置 Webhook 接收实时事件",
     "Webhook 让您在事件发生时实时接收推送。请进入 设置>集成>Webhooks，点击「添加端点」，填写您的接收 URL（必须是 HTTPS）。"
     "选择要订阅的事件（如 task.created、task.completed）。NimbusFlow 会以 POST 方式发送 JSON 负载，并在请求头附带签名（X-Nimbus-Signature），"
     "您应使用端点密钥（signing secret）校验签名以确保来源可信。若您的服务返回非 2xx 状态，系统会按指数退避重试最多 5 次。"),

    ("integrations_api", "OAuth 授权失败的常见原因",
     "在连接第三方应用时遇到 OAuth 授权失败，常见原因包括：回调 URL（redirect URI）与应用注册时填写的不一致；"
     "授权 token 已过期；或浏览器拦截了第三方 Cookie。请先确认在第三方平台后台登记的回调地址与 NimbusFlow 文档中给出的完全一致。"
     "若提示 invalid_scope，说明请求的权限范围超出了应用被授予的范围，需在应用设置中调整 scope。清除浏览器缓存后重试通常能解决临时性问题。"),

    # ---- data_export ----
    ("data_export", "如何导出 NimbusFlow 中的数据",
     "您可以将工作区数据导出备份。请进入 设置>数据>导出，选择要导出的内容（任务、项目、评论、附件），再选择格式 CSV 或 JSON。"
     "点击「开始导出」后系统会在后台打包，完成后会发送一封带下载链接的邮件。导出文件链接 7天内有效，过期需重新发起导出。"
     "大型工作区的导出可能耗时数分钟到数小时。附件会以 zip 形式单独打包。仅工作区管理员或拥有「导出」权限的成员可以发起导出。"),

    ("data_export", "导出文件的格式与字段说明",
     "NimbusFlow 导出支持 CSV 与 JSON 两种格式。CSV 适合在 Excel 或 Google Sheets 中查看，每种对象（任务、项目）对应一个独立文件；"
     "JSON 保留完整的层级结构与关联关系，适合程序化导入到其他系统。任务字段包括 id、title、status、assignee、due_date、created_at 等。"
     "时间戳统一采用 UTC 时区的 ISO 8601 格式。富文本评论在 CSV 中会被转为纯文本，在 JSON 中保留原始 Markdown。"),

    ("data_export", "如何申请删除账户及全部数据（GDPR）",
     "根据数据保护法规，您有权要求删除个人数据。账户拥有者可进入 设置>数据>删除账户 发起永久删除请求。"
     "系统会要求二次确认并输入密码。提交后有 14天冷静期，期间您可随时取消；冷静期结束后数据将被不可逆删除，包括所有备份。"
     "删除前请务必先导出需要保留的数据。若您是被邀请的成员而非拥有者，只能删除自己的个人资料，无法删除整个工作区。"),

    # ---- permissions ----
    ("permissions", "NimbusFlow 的角色与权限说明",
     "NimbusFlow 提供四种内置角色：Owner（拥有者）、Admin（管理员）、Member（成员）和 Guest（访客）。"
     "Owner 拥有最高权限，包括计费、删除工作区；Admin 可管理成员、集成与安全设置但不能删除工作区；"
     "Member 可创建和编辑被授权项目中的内容；Guest 仅能访问被明确分享的特定项目，且无法看到其他项目。"
     "角色在 设置>成员 页面分配。一个工作区可有多个 Admin，但 Owner 通常只有一个，可通过「转移所有权」变更。"),

    ("permissions", "如何邀请新成员加入工作区",
     "邀请同事加入很简单。请进入 设置>成员，点击「邀请成员」，输入对方邮箱并选择要分配的角色（Member、Admin 或 Guest）。"
     "对方会收到一封邀请邮件，点击链接注册或登录后即可加入。邀请链接 默认7天后过期，过期可重新发送。"
     "您也可以生成一个通用邀请链接分享到群里，但出于安全建议仅对可信成员使用。付费套餐按席位（seat）计费，超出已购席位需先增购。"),

    ("permissions", "如何转移工作区所有权",
     "若需要将工作区 Owner 转交给他人（如负责人离职），当前 Owner 请进入 设置>成员，找到目标成员，点击「设为拥有者」并确认。"
     "目标成员必须已经是工作区的 Admin 或 Member。转移后原 Owner 会自动降级为 Admin。所有权转移会一并转移计费责任，"
     "因此请确保新 Owner 知悉后续续费由其负责。每个工作区同一时间只能有一位 Owner。此操作需要原 Owner 输入密码二次确认。"),

    # ---- mobile_app ----
    ("mobile_app", "NimbusFlow 移动端推送通知设置",
     "在手机上及时收到提醒，请确保已开启推送。打开 NimbusFlow App，进入「我的>通知设置」，开启「推送通知」总开关，"
     "再按类型（任务分配、截止提醒、@ 提及、评论回复）单独勾选。还需在手机系统设置中允许 NimbusFlow 发送通知。"
     "如果收不到推送，请检查是否开启了系统级「勿扰模式」或为 App 限制了后台刷新。安卓用户请关闭电池优化中对本应用的限制。"),

    ("mobile_app", "如何在移动端离线使用 NimbusFlow",
     "NimbusFlow 移动端支持有限的离线模式。当网络中断时，您仍可查看已缓存的任务和项目，并新建或编辑任务；"
     "这些更改会暂存在本地，待网络恢复后自动同步到云端。请注意离线期间无法加载未缓存过的内容，也无法上传大附件。"
     "若多人在离线期间编辑了同一条目，恢复联网后可能产生冲突，系统会保留双方版本并提示您手动合并。建议联网时打开常用项目以预缓存数据。"),

    ("mobile_app", "移动端登录与生物识别",
     "移动端支持指纹或面容（Face ID）快速登录。首次需用邮箱密码正常登录，随后在 App 的「我的>安全」中开启「生物识别解锁」。"
     "开启后再次打开 App 只需指纹或面容即可，无需重复输入密码。该功能依赖手机系统本身已设置的生物识别。"
     "若更换手机或重置了系统生物识别，需重新用密码登录并再次开启。出于安全，连续多次生物识别失败后会要求输入账户密码。"),

    # ---- troubleshooting ----
    ("troubleshooting", "页面加载缓慢或卡顿的排查方法",
     "若 NimbusFlow 网页端加载缓慢，请按以下步骤排查：首先刷新页面并清除浏览器缓存；其次确认网络连接稳定，可尝试切换网络；"
     "再次确认浏览器为最新版本（推荐 Chrome、Edge、Firefox 最新版），过旧版本可能不兼容。"
     "若仅某个大型项目卡顿，可能是任务数量过多导致，建议归档已完成项目。您也可访问 status.nimbusflow.example 查看是否存在平台级故障。"
     "禁用可能冲突的浏览器扩展（如广告拦截、脚本管理器）也常能解决问题。"),

    ("troubleshooting", "通知邮件收不到怎么办",
     "如果一直收不到 NimbusFlow 的通知邮件，请逐项检查：确认 设置>通知 中对应的邮件通知已开启；"
     "检查邮箱的垃圾邮件和促销文件夹；将发件域名 nimbusflow.example 加入白名单（whitelist）。"
     "企业邮箱可能被 IT 部门的反垃圾网关拦截，可请管理员放行。若您近期更改过账户邮箱，请确认新邮箱已验证。"
     "系统通知邮件通常在事件发生后几分钟内发出，高峰期可能略有延迟。"),

    ("troubleshooting", "上传附件失败的常见原因",
     "上传附件失败通常与以下因素有关：单个文件超过大小上限（免费版 25 MB，付费版 100 MB）；文件类型受限（出于安全，可执行文件如 .exe 被禁止）；"
     "或网络在上传中途中断。请先确认文件大小与类型符合要求，再重试。"
     "若上传到一半失败，建议刷新页面后重新上传，不要在多个标签页同时上传同一文件。"
     "工作区存储空间已满时也会上传失败，可在 设置>数据>存储用量 中查看剩余空间并清理或升级套餐。"),

    ("troubleshooting", "任务或项目「找不到了」的恢复方法",
     "找不到某个任务或项目，多数情况并非真正丢失。请先检查是否被误归档：进入项目列表，打开「已归档」筛选查看。"
     "其次确认您是否仍有该项目的访问权限——被移出项目后将无法看到它。被删除的项目会进入回收站（Trash），"
     "拥有者或管理员可在 设置>数据>回收站 中找到并在 30天内还原；超过 30 天将被永久清除。若确认是他人误删，请联系工作区管理员协助还原。"),

    # ---- general ----
    ("general", "NimbusFlow 是什么：产品概览",
     "NimbusFlow 是一款面向团队的 SaaS 协作办公产品，帮助团队在一个地方管理任务、项目、文档与沟通。"
     "核心功能包括看板与列表视图的任务管理、项目时间线、文件共享、评论与 @ 提及、以及与 Slack、Google 日历等工具的集成。"
     "它提供 Web 端与 iOS/Android 移动端，数据实时云端同步。NimbusFlow 适合从几人的小团队到数百人的企业使用，"
     "并按 Starter、Team、Business 三档套餐提供不同的功能与席位上限。"),

    ("general", "如何联系 NimbusFlow 客户支持",
     "遇到问题需要帮助时，您可以通过多种渠道联系我们。最快的方式是点击产品右下角的「帮助」气泡发起在线对话（工作日 9:00–18:00）。"
     "您也可以发送邮件至 support@nimbusflow.example，我们通常在一个工作日内回复。"
     "涉及账单与发票的问题，请在工单中注明，会转交账单团队处理；涉及安全或账号被盗的紧急情况，请在标题标注「紧急」。"
     "Business 套餐客户享有专属客户成功经理（CSM）与优先响应。"),

    ("general", "键盘快捷键速查",
     "熟练使用快捷键能大幅提升效率。在 NimbusFlow Web 端，按 ? 可随时唤出快捷键面板。"
     "常用快捷键包括：C 新建任务、/ 聚焦搜索框、G 后接 P 跳转项目、E 编辑当前任务、Cmd/Ctrl+K 打开全局命令面板。"
     "在任务详情中按 A 可快速指派负责人，按 D 设置截止日期。快捷键可在 设置>偏好 中查看完整列表，部分快捷键支持自定义。"),

    ("general", "调整界面语言与时区",
     "NimbusFlow 支持多语言界面与时区设置。请进入 设置>偏好，在「语言」下拉中选择简体中文、English、日本語等；"
     "在「时区」中选择您所在的时区，所有截止时间与时间戳会按此显示。修改后立即生效，无需重新登录。"
     "团队成员可各自设置不同语言与时区，互不影响。若您发现日期显示与实际相差几小时，多半是时区设置不正确，请优先检查此处。"),

    ("general", "数据安全与合规概览",
     "NimbusFlow 高度重视数据安全。所有数据传输采用 TLS 加密，静态数据采用 AES-256 加密存储。"
     "我们的基础设施托管在通过 SOC 2 Type II 与 ISO 27001 认证的云平台上，并定期进行第三方渗透测试。"
     "Business 套餐支持单点登录（SSO / SAML）、审计日志导出与数据驻留区域选择。我们遵守 GDPR 与 CCPA，用户可随时导出或删除自己的数据。"
     "如需签署数据处理协议（DPA），请联系销售或合规团队。"),
]

# Assign doc_ids
docs = []
for i, (topic, title, body) in enumerate(KB, start=1):
    doc_id = "kb_%03d" % i
    docs.append({"doc_id": doc_id, "title": title, "topic": topic, "text": body})

# ---------------------------------------------------------------------------
# 2) Queries
#    Helper to build a group with train + eval variants sharing keypoints.
# ---------------------------------------------------------------------------
queries = []
_qcounter = [0]

def add(group, split, query, keypoints, gold, escalate, difficulty, resolution):
    _qcounter[0] += 1
    qid = "q%03d" % _qcounter[0]
    queries.append({
        "id": qid,
        "split": split,
        "group": group,
        "query": query,
        "required_keypoints": keypoints,
        "gold_doc_ids": gold,
        "should_escalate": escalate,
        "difficulty": difficulty,
        "resolution": resolution,
    })

# Map doc titles -> id for convenience
DID = {d["title"]: d["doc_id"] for d in docs}

# ============ EASY groups (keypoints fully inside gold doc) ============

# g01 easy account_security - reset password
add("g01", "train", "我忘记密码了怎么重置",
    ["忘记密码", "15分钟内有效", "至少 8 位"],
    [DID["如何重置 NimbusFlow 登录密码"]], False, "easy",
    "请在登录页点击「忘记密码」并输入注册邮箱，系统会发送重置邮件，链接 15分钟内有效。点击后设置新密码，新密码至少 8 位且需含字母与数字。")
add("g01", "eval", "登陆密码记不住了，怎样重新设置一个新密码",
    ["忘记密码", "15分钟内有效", "至少 8 位"],
    [DID["如何重置 NimbusFlow 登录密码"]], False, "easy",
    "在登录页点击「忘记密码」，输入注册邮箱后会收到重置邮件，链接 15分钟内有效。打开链接即可设置新密码，新密码至少 8 位并包含字母与数字。")

# g02 easy account_security - 2FA
add("g02", "train", "怎么开启两步验证",
    ["进入 设置>安全", "恢复码", "TOTP"],
    [DID["开启两步验证（2FA）保护账户"]], False, "easy",
    "请进入 设置>安全，打开「两步验证」开关，选择验证器 App 的 TOTP 或短信方式。开启后请妥善保存系统生成的恢复码，手机丢失时用它登录。")
add("g02", "eval", "我想给账号加个2fa保护，在哪设置",
    ["进入 设置>安全", "恢复码", "TOTP"],
    [DID["开启两步验证（2FA）保护账户"]], False, "easy",
    "请进入 设置>安全 找到「两步验证」并开启，可选 TOTP 验证器 App 或短信。务必保存好恢复码，这是手机丢失后登录的唯一方式。")

# g03 easy billing - invoice download
add("g03", "train", "在哪里下载发票",
    ["设置>账单>发票历史", "下载 PDF", "税号"],
    [DID["NimbusFlow 发票与收据下载指南"]], False, "easy",
    "请进入 设置>账单>发票历史，找到对应月份记录后点击「下载 PDF」。若需公司抬头或税号，请先在账单信息中填写，新发票会自动包含。")
add("g03", "eval", "发票怎么弄下来，还要加公司税号",
    ["设置>账单>发票历史", "下载 PDF", "税号"],
    [DID["NimbusFlow 发票与收据下载指南"]], False, "easy",
    "在 设置>账单>发票历史 里选择对应月份点击「下载 PDF」即可。要在发票上显示税号，请到账单信息中填写税号后再生成发票。")

# g04 easy integrations_api - API key
add("g04", "train", "怎么生成 API key",
    ["设置>集成>API", "Bearer", "只会完整显示一次"],
    [DID["如何创建和管理 NimbusFlow API Key"]], False, "easy",
    "请进入 设置>集成>API 点击「生成新密钥」，命名并选择权限范围。密钥只会完整显示一次，请立即复制保存。请求时用 Authorization: Bearer <token> 携带。")
add("g04", "eval", "我要调用接口，apikey在哪创建",
    ["设置>集成>API", "Bearer", "只会完整显示一次"],
    [DID["如何创建和管理 NimbusFlow API Key"]], False, "easy",
    "在 设置>集成>API 点「生成新密钥」即可创建 API Key。注意它只会完整显示一次，务必当场复制。调用时在请求头用 Authorization: Bearer <token>。")

# g05 easy data_export - export data
add("g05", "train", "怎么把数据导出来备份",
    ["设置>数据>导出", "CSV 或 JSON", "7天内有效"],
    [DID["如何导出 NimbusFlow 中的数据"]], False, "easy",
    "请进入 设置>数据>导出，选择要导出的内容与格式 CSV 或 JSON，点击开始导出。完成后会邮件发送下载链接，该链接 7天内有效。")
add("g05", "eval", "想导出全部任务做个备份，支持什么格式",
    ["设置>数据>导出", "CSV 或 JSON", "7天内有效"],
    [DID["如何导出 NimbusFlow 中的数据"]], False, "easy",
    "在 设置>数据>导出 中可选 CSV 或 JSON 格式导出任务等内容。导出完成后系统邮件发送下载链接，链接 7天内有效，过期需重新导出。")

# g06 easy permissions - roles
add("g06", "train", "成员都有哪些角色，权限有什么区别",
    ["Owner", "Admin", "Guest"],
    [DID["NimbusFlow 的角色与权限说明"]], False, "easy",
    "NimbusFlow 有四种角色：Owner 拥有最高权限含计费与删除工作区；Admin 可管理成员与设置；Member 编辑被授权内容；Guest 仅能访问被分享的特定项目。")
add("g06", "eval", "guest 和 admin 有什么不一样",
    ["Owner", "Admin", "Guest"],
    [DID["NimbusFlow 的角色与权限说明"]], False, "easy",
    "Admin 可管理成员、集成与安全设置，权限很高仅次于 Owner；Guest 只能访问被明确分享的项目，看不到其他项目。Owner 则拥有计费等最高权限。")

# g07 easy mobile_app - push
add("g07", "train", "手机上收不到推送通知",
    ["我的>通知设置", "推送通知", "勿扰模式"],
    [DID["NimbusFlow 移动端推送通知设置"]], False, "easy",
    "请在 App 的「我的>通知设置」开启「推送通知」总开关并按类型勾选，同时在手机系统设置允许通知。若仍收不到，检查是否开启了系统级勿扰模式。")
add("g07", "eval", "app的提醒一直不弹出来咋办",
    ["我的>通知设置", "推送通知", "勿扰模式"],
    [DID["NimbusFlow 移动端推送通知设置"]], False, "easy",
    "先到「我的>通知设置」确认「推送通知」已开启并选择了通知类型，再到手机系统设置允许 NimbusFlow 发通知。也请确认没有开启勿扰模式。")

# g08 easy troubleshooting - upload fail
add("g08", "train", "上传附件总是失败",
    ["免费版 25 MB", "付费版 100 MB", ".exe 被禁止"],
    [DID["上传附件失败的常见原因"]], False, "easy",
    "上传失败多因文件超限：免费版 25 MB、付费版 100 MB，超过会失败；同时可执行文件如 .exe 被禁止。请确认文件大小与类型符合要求后重试。")
add("g08", "eval", "为啥我的文件传不上去",
    ["免费版 25 MB", "付费版 100 MB", ".exe 被禁止"],
    [DID["上传附件失败的常见原因"]], False, "easy",
    "请检查文件大小是否超限，免费版 25 MB、付费版 100 MB；另外 .exe 被禁止上传。符合要求后刷新页面重试即可。")

# g09 easy troubleshooting - slow page
add("g09", "train", "网页特别卡，加载很慢",
    ["清除浏览器缓存", "最新版本", "归档已完成项目"],
    [DID["页面加载缓慢或卡顿的排查方法"]], False, "easy",
    "请先刷新并清除浏览器缓存，确认浏览器为最新版本。若某个大型项目卡顿，是任务过多所致，建议归档已完成项目以提速。")
add("g09", "eval", "打开页面好慢啊 一直转圈",
    ["清除浏览器缓存", "最新版本", "归档已完成项目"],
    [DID["页面加载缓慢或卡顿的排查方法"]], False, "easy",
    "建议清除浏览器缓存并更新到最新版本浏览器。如果是单个大项目卡，多半是任务太多，归档已完成项目通常能明显改善。")

# g10 easy general - language/timezone
add("g10", "train", "怎么把界面改成中文，时间也不对",
    ["设置>偏好", "语言", "时区"],
    [DID["调整界面语言与时区"]], False, "easy",
    "请进入 设置>偏好，在「语言」中选简体中文，在「时区」中选择您所在时区，截止时间会按此显示。修改后立即生效。日期偏差通常是时区设置不对。")
add("g10", "eval", "日期显示差了几个小时，在哪调时区",
    ["设置>偏好", "语言", "时区"],
    [DID["调整界面语言与时区"]], False, "easy",
    "到 设置>偏好 检查「时区」是否正确，日期差几小时通常就是时区没设对；同一页面的「语言」也可改成中文。改完立即生效。")

# g11 easy integrations_api - slack
add("g11", "train", "怎么把 slack 连上 NimbusFlow",
    ["设置>集成>应用市场", "OAuth 授权", "管理员权限"],
    [DID["连接 Slack 与 NimbusFlow"]], False, "easy",
    "请进入 设置>集成>应用市场 找到 Slack 点「连接」，在 OAuth 授权页登录 Slack 工作区并授权，再选择接收通知的频道。首次安装需要 Slack 工作区管理员权限。")
add("g11", "eval", "想让任务更新推到slack频道里",
    ["设置>集成>应用市场", "OAuth 授权", "管理员权限"],
    [DID["连接 Slack 与 NimbusFlow"]], False, "easy",
    "在 设置>集成>应用市场 连接 Slack，完成 OAuth 授权后选择目标频道与事件即可把任务更新推过去。注意首次安装需要 Slack 工作区管理员权限。")

# g12 easy permissions - invite
add("g12", "train", "怎么邀请同事进来",
    ["设置>成员", "邀请成员", "默认7天后过期"],
    [DID["如何邀请新成员加入工作区"]], False, "easy",
    "请进入 设置>成员 点「邀请成员」，输入对方邮箱并选择角色。对方收到邀请邮件点链接即可加入，邀请链接默认7天后过期，过期可重新发送。")
add("g12", "eval", "拉新人加入团队的入口在哪",
    ["设置>成员", "邀请成员", "默认7天后过期"],
    [DID["如何邀请新成员加入工作区"]], False, "easy",
    "在 设置>成员 点击「邀请成员」输入邮箱并选角色即可发出邀请。邀请链接默认7天后过期，若过期重新发送一次就行。")

# g13 easy account_security - sessions
add("g13", "train", "怎么看哪些设备登录了我的账号",
    ["设置>安全>活动会话", "登出所有其他设备"],
    [DID["管理已登录设备与会话"]], False, "easy",
    "请进入 设置>安全>活动会话 查看所有登录设备与最近活动。发现陌生设备点「登出」，或点「登出所有其他设备」一键清除其余会话。")
add("g13", "eval", "我想踢掉其他设备的登录状态",
    ["设置>安全>活动会话", "登出所有其他设备"],
    [DID["管理已登录设备与会话"]], False, "easy",
    "到 设置>安全>活动会话 即可管理登录设备，点「登出所有其他设备」就能把当前设备以外的会话全部清掉。")

# g14 easy troubleshooting - lost task
add("g14", "train", "我的项目突然找不到了",
    ["已归档", "回收站", "30天内"],
    [DID["任务或项目「找不到了」的恢复方法"]], False, "easy",
    "项目多半没真丢。先在项目列表打开「已归档」筛选查看；被删除的项目会进回收站，管理员可在 设置>数据>回收站 中 30天内还原。")
add("g14", "eval", "之前的任务列表不见了 是被删了吗",
    ["已归档", "回收站", "30天内"],
    [DID["任务或项目「找不到了」的恢复方法"]], False, "easy",
    "先检查是不是被误归档了（打开「已归档」筛选）。如果确实被删除，会进回收站，30天内 管理员能在 设置>数据>回收站 里还原。")

# ============ HARD groups (at least one keypoint NOT fully in gold docs) ============
# These represent tribal knowledge / coverage gaps. The "tribal" keypoint is the
# specific operational detail that does NOT appear verbatim in any KB doc.

# g15 hard billing - annual->monthly switch credit timing (tribal: 具体抵扣是下个完整周期)
add("g15", "train", "我从年付改成月付，多付的钱什么时候退",
    ["account credit", "不退回原卡", "下次续费自动抵扣"],
    [DID["如何升级或降级 NimbusFlow 订阅套餐"]], False, "hard",
    "从年付改月付属于降级，差额并不退回原卡，而是转为 account credit 留在账户里，并在下次续费自动抵扣，不能提现。")
add("g15", "eval", "年付转月付剩下的余额会打回我银行卡吗",
    ["account credit", "不退回原卡", "下次续费自动抵扣"],
    [DID["如何升级或降级 NimbusFlow 订阅套餐"]], False, "hard",
    "余额并不退回原卡。年付转月付的余额以 account credit 形式保留，并在下次续费自动抵扣到账单里。")

# g16 hard account_security - account compromised -> escalate
add("g16", "train", "我的账号被盗了，有人改了我的资料",
    ["立即重置密码", "登出所有其他设备", "转接人工安全团队"],
    [DID["管理已登录设备与会话"]], True, "hard",
    "请立即重置密码并到活动会话「登出所有其他设备」，开启两步验证。由于涉及账号被盗，我已为您转接人工安全团队进一步核查异常登录与资金风险。")
add("g16", "eval", "好像有人黑进了我的账户怎么办",
    ["立即重置密码", "登出所有其他设备", "转接人工安全团队"],
    [DID["管理已登录设备与会话"]], True, "hard",
    "先立即重置密码，并在活动会话里登出所有其他设备，开启 2FA。账号被盗属于安全事件，我已转接人工安全团队为您处理。")

# g17 hard billing - refund dispute -> escalate (tribal: 退款政策14天, 转账单团队)
add("g17", "train", "我要求全额退款，你们多扣了我一个月",
    ["转接人工账单团队", "退款需人工核实", "14天内可申请退款"],
    [], True, "hard",
    "退款需人工核实金额，自助无法处理。NimbusFlow 政策为购买后 14天内可申请退款。由于涉及退款金额争议，我已转接人工账单团队为您核实并处理。")
add("g17", "eval", "这个月被乱收费了 我想退钱",
    ["转接人工账单团队", "退款需人工核实", "14天内可申请退款"],
    [], True, "hard",
    "退款需人工核实具体扣费记录，按政策购买后 14天内可申请退款。涉及金额争议，我已为您转接人工账单团队跟进退款。")

# g18 hard integrations_api - webhook signature verification recipe (tribal: HMAC-SHA256, 5分钟时间窗)
add("g18", "train", "webhook 收到的请求怎么验证是不是你们发的",
    ["X-Nimbus-Signature", "HMAC-SHA256", "5分钟时间窗内"],
    [DID["设置 Webhook 接收实时事件"]], False, "hard",
    "用端点的 signing secret 对原始请求体计算 HMAC-SHA256，与请求头 X-Nimbus-Signature 比对一致即可信；同时校验时间戳是否在 5分钟时间窗内以防重放攻击。")
add("g18", "eval", "怎么校验 webhook 签名防止被伪造",
    ["X-Nimbus-Signature", "HMAC-SHA256", "5分钟时间窗内"],
    [DID["设置 Webhook 接收实时事件"]], False, "hard",
    "对请求体用 signing secret 做 HMAC-SHA256，结果和 X-Nimbus-Signature 头比对；并确认请求时间戳在 5分钟时间窗内，避免重放。")

# g19 hard data_export - export then reimport to new workspace (tribal: 联系支持开启迁移工具)
add("g19", "train", "导出的数据能直接导回到一个新工作区吗",
    ["JSON", "不支持自助导入", "联系支持开启迁移工具"],
    [DID["导出文件的格式与字段说明"]], False, "hard",
    "导出的 JSON 保留了完整层级，但目前不支持自助导入到新工作区。整库迁移需联系支持开启迁移工具，由我们后台协助导入。")
add("g19", "eval", "我想把数据搬到另一个新的工作区，怎么搞",
    ["JSON", "不支持自助导入", "联系支持开启迁移工具"],
    [DID["导出文件的格式与字段说明"]], False, "hard",
    "可以先导出 JSON，但工作区之间不支持自助导入。要做整库迁移请联系支持开启迁移工具，我们会在后台帮您导入。")

# g20 hard permissions - last owner cannot leave (tribal: 必须先转移所有权)
add("g20", "train", "我是唯一的 owner，想退出这个工作区但退不了",
    ["唯一的 Owner 无法直接退出", "先转移所有权", "设置>成员"],
    [DID["如何转移工作区所有权"]], False, "hard",
    "作为唯一的 Owner 无法直接退出，否则工作区会失去负责人。您需要先转移所有权给另一位成员（在 设置>成员 操作），转移后您降为 Admin 即可退出。")
add("g20", "eval", "工作区就我一个拥有者，怎么才能离开",
    ["唯一的 Owner 无法直接退出", "先转移所有权", "设置>成员"],
    [DID["如何转移工作区所有权"]], False, "hard",
    "唯一的 Owner 无法直接退出。请到 设置>成员 先转移所有权给他人，之后您会变成 Admin，就可以离开工作区了。")

# g21 hard mobile_app - offline conflict resolution detail (tribal: 保留双方版本+_conflict后缀)
add("g21", "train", "离线时两个人改了同一个任务，联网后会怎样",
    ["保留双方版本", "提示您手动合并", "生成 _conflict 副本"],
    [DID["如何在移动端离线使用 NimbusFlow"]], False, "hard",
    "离线期间多人改同一条目，联网后系统不会自动覆盖，而是保留双方版本，并生成 _conflict 副本，提示您手动合并冲突字段。")
add("g21", "eval", "断网编辑撞车了，数据会不会被覆盖丢掉",
    ["保留双方版本", "提示您手动合并", "生成 _conflict 副本"],
    [DID["如何在移动端离线使用 NimbusFlow"]], False, "hard",
    "不会丢。系统会保留双方版本，生成 _conflict 副本，并提示您手动合并，不会直接覆盖。")

# g22 hard troubleshooting - email not received: SPF/DMARC tribal
add("g22", "train", "企业邮箱一直收不到通知邮件，白名单也加了",
    ["nimbusflow.example 加入白名单", "SPF 与 DMARC 校验", "放行发件 IP 段"],
    [DID["通知邮件收不到怎么办"]], False, "hard",
    "若已把 nimbusflow.example 加入白名单仍收不到，多半是企业网关的 SPF 与 DMARC 校验拦截。请让 IT 在反垃圾网关放行发件 IP 段并通过 SPF 与 DMARC 校验。")
add("g22", "eval", "公司邮箱收不到提醒邮件 IT那边要怎么配置",
    ["nimbusflow.example 加入白名单", "SPF 与 DMARC 校验", "放行发件 IP 段"],
    [DID["通知邮件收不到怎么办"]], False, "hard",
    "请 IT 把 nimbusflow.example 加入白名单，并在网关放行发件 IP 段、通过 SPF 与 DMARC 校验，企业邮箱拦截通常就是卡在这里。")

# g23 hard account_security - SSO enforced can't login w/ password (tribal: Business SSO bypass code)
add("g23", "train", "公司开了 SSO 后我用密码登不进去了",
    ["改用 SSO 入口登录", "强制 SSO", "向管理员申请临时 bypass 码"],
    [DID["数据安全与合规概览"]], False, "hard",
    "开启 SSO 后账户进入强制 SSO 模式，不能再用密码登录，请改用 SSO 入口登录。若 SSO 暂时不可用，需向管理员申请临时 bypass 码应急登录。")
add("g23", "eval", "启用单点登录之后原来的密码失效了",
    ["改用 SSO 入口登录", "强制 SSO", "向管理员申请临时 bypass 码"],
    [DID["数据安全与合规概览"]], False, "hard",
    "这是强制 SSO 生效了，原密码不再可用，请改用 SSO 入口登录。万一 SSO 故障，可向管理员申请临时 bypass 码登录。")

# g24 hard integrations_api - rate limit 429 best practice (tribal: Retry-After头+指数退避)
add("g24", "train", "调接口老是返回 429 怎么处理",
    ["每分钟 600 次", "读取 Retry-After 头", "指数退避重试"],
    [DID["如何创建和管理 NimbusFlow API Key"]], False, "hard",
    "429 表示触发了每分钟 600 次的速率限制。正确做法是读取 Retry-After 头等待相应秒数，并配合指数退避重试，而不是立即重发。")
add("g24", "eval", "api 请求被限流了，最佳处理方式是什么",
    ["每分钟 600 次", "读取 Retry-After 头", "指数退避重试"],
    [DID["如何创建和管理 NimbusFlow API Key"]], False, "hard",
    "限流上限是每分钟 600 次。遇到 429 请读取 Retry-After 头按提示等待，并用指数退避重试，避免短时间内继续打满。")

# g25 hard billing - failed payment grace period exact (tribal: 7天宽限+只读)
add("g25", "train", "信用卡扣费失败了，我的服务会马上停吗",
    ["grace period", "7天宽限期", "期间变为只读"],
    [DID["如何更换 NimbusFlow 的付款方式"]], False, "hard",
    "不会立刻停。扣费失败后账户进入 grace period，共有 7天宽限期，期间变为只读、数据保留。请在宽限期内更新付款方式以恢复正常。")
add("g25", "eval", "卡扣不上钱 是不是数据就没了",
    ["grace period", "7天宽限期", "期间变为只读"],
    [DID["如何更换 NimbusFlow 的付款方式"]], False, "hard",
    "数据不会丢。扣费失败进入 grace period，有 7天宽限期，期间变为只读但数据保留，及时更新付款方式即可恢复。")

# g26 hard general - enterprise contract terms -> escalate
add("g26", "train", "我们想签年度企业合同，能改合同里的责任条款吗",
    ["合同条款需法务确认", "转接销售与法务团队", "提供 DPA"],
    [DID["数据安全与合规概览"]], True, "hard",
    "企业合同条款需法务确认，无法在自助渠道修改。我们可提供 DPA 等文件。由于涉及合同条款，我已为您转接销售与法务团队对接。")
add("g26", "eval", "签大客户合同想谈一下里面的法律条款",
    ["合同条款需法务确认", "转接销售与法务团队", "提供 DPA"],
    [DID["数据安全与合规概览"]], True, "hard",
    "合同条款需法务确认，不能自助改动；我们可提供 DPA。涉及合同，我已转接销售与法务团队与您对接。")

# g27 hard mobile_app - biometric lockout recovery (tribal: 5次失败后锁定+需密码+清缓存重绑)
add("g27", "train", "面容登录一直失败，现在打不开 app 了",
    ["多次失败后要求输入账户密码", "用邮箱密码重新登录", "重新开启生物识别"],
    [DID["移动端登录与生物识别"]], False, "hard",
    "这是多次失败后要求输入账户密码的保护机制。请用邮箱密码重新登录 App，登录后再到「我的>安全」重新开启生物识别即可恢复面容登录。")
add("g27", "eval", "指纹解锁老是不识别 进不去应用",
    ["多次失败后要求输入账户密码", "用邮箱密码重新登录", "重新开启生物识别"],
    [DID["移动端登录与生物识别"]], False, "hard",
    "这是多次失败后要求输入账户密码的机制。请先用邮箱密码重新登录，再在「我的>安全」里重新开启生物识别，指纹解锁就会恢复。")

# g28 hard troubleshooting - storage full cleanup (tribal: 清空回收站才真正释放)
add("g28", "train", "存储空间满了，删了任务怎么还是满的",
    ["设置>数据>存储用量", "回收站仍占用空间", "清空回收站才释放"],
    [DID["上传附件失败的常见原因"], DID["任务或项目「找不到了」的恢复方法"]], False, "hard",
    "删除的内容会进回收站，回收站仍占用空间，所以删了也不释放。请在 设置>数据>存储用量 查看占用，清空回收站才释放空间。")
add("g28", "eval", "明明删了好多东西 存储还是显示满的",
    ["设置>数据>存储用量", "回收站仍占用空间", "清空回收站才释放"],
    [DID["上传附件失败的常见原因"], DID["任务或项目「找不到了」的恢复方法"]], False, "hard",
    "因为删除项进了回收站，回收站仍占用空间。到 设置>数据>存储用量 查看后清空回收站才释放，空间就会降下来。")

# g29 hard data_export - GDPR deletion is irreversible + escalate confirmation (tribal: 14天冷静期, 不可逆)
add("g29", "train", "我要永久删除整个账户和所有数据，能恢复吗",
    ["设置>数据>删除账户", "14天冷静期", "不可逆删除", "转人工确认"],
    [DID["如何申请删除账户及全部数据（GDPR）"]], True, "hard",
    "账户拥有者可在 设置>数据>删除账户 发起。提交后有 14天冷静期可取消，之后是不可逆删除且无法恢复。由于操作不可逆且影响整库，我已转人工确认您的身份与意图。")
add("g29", "eval", "彻底注销账号删光数据，删了还能找回来不",
    ["设置>数据>删除账户", "14天冷静期", "不可逆删除", "转人工确认"],
    [DID["如何申请删除账户及全部数据（GDPR）"]], True, "hard",
    "在 设置>数据>删除账户 发起，有 14天冷静期，冷静期后为不可逆删除，无法找回。因不可逆，我已转人工确认身份后再执行。")

# g30 hard integrations_api - OAuth invalid_scope deep fix (tribal: 重新授权+管理员批准scope)
add("g30", "train", "连接第三方应用提示 invalid_scope",
    ["请求的权限范围超出", "调整 scope 后重新授权", "需管理员批准新权限"],
    [DID["OAuth 授权失败的常见原因"]], False, "hard",
    "invalid_scope 表示请求的权限范围超出了应用被授予的范围。请在应用设置中调整 scope 后重新授权；若新增了敏感权限，还需管理员批准新权限才能生效。")
add("g30", "eval", "oauth 老报 invalid_scope 错误",
    ["请求的权限范围超出", "调整 scope 后重新授权", "需管理员批准新权限"],
    [DID["OAuth 授权失败的常见原因"]], False, "hard",
    "这是请求的权限范围超出了应用被授予的范围。请在应用设置里调整 scope 后重新授权，若涉及新的敏感权限需管理员批准新权限。")

# ============ extra EASY to balance topic coverage ============
# g31 easy general - what is NimbusFlow
add("g31", "train", "NimbusFlow 到底是干嘛的",
    ["面向团队", "任务", "套餐"],
    [DID["NimbusFlow 是什么：产品概览"]], False, "easy",
    "NimbusFlow 是一款面向团队的协作办公产品，集中管理任务、项目、文档与沟通，提供 Web 与移动端，并按 Starter、Team、Business 三档套餐提供不同功能。")
add("g31", "eval", "这个产品是做什么用的，适合谁",
    ["面向团队", "任务", "套餐"],
    [DID["NimbusFlow 是什么：产品概览"]], False, "easy",
    "它是面向团队的协作办公工具，核心是任务与项目管理，适合小团队到企业使用，按不同套餐提供功能差异。")

# g32 easy general - contact support
add("g32", "train", "怎么联系你们客服",
    ["右下角的「帮助」", "support@nimbusflow.example", "一个工作日内"],
    [DID["如何联系 NimbusFlow 客户支持"]], False, "easy",
    "您可点击产品右下角的「帮助」气泡发起在线对话，或发邮件到 support@nimbusflow.example，我们通常一个工作日内回复。")
add("g32", "eval", "有问题想找人工，从哪里联系",
    ["右下角的「帮助」", "support@nimbusflow.example", "一个工作日内"],
    [DID["如何联系 NimbusFlow 客户支持"]], False, "easy",
    "最快是点右下角的「帮助」气泡在线对话；也可邮件 support@nimbusflow.example，通常一个工作日内回复。")

# g33 easy account_security - locked account
add("g33", "train", "提示账户被锁定了登不进去",
    ["连续 5 次", "锁定账户 30 分钟", "忘记密码"],
    [DID["登录时提示「账户已锁定」怎么办"]], False, "easy",
    "连续 5 次输错密码会锁定账户 30 分钟以防暴力破解。等待 30 分钟自动解除，或走「忘记密码」重置密码立即解锁。")
add("g33", "eval", "密码输错几次现在被锁了多久能好",
    ["连续 5 次", "锁定账户 30 分钟", "忘记密码"],
    [DID["登录时提示「账户已锁定」怎么办"]], False, "easy",
    "连续 5 次错误会锁定账户 30 分钟，等 30 分钟自动解锁；想立刻解锁就用「忘记密码」重置密码。")

# g34 easy general - shortcuts
add("g34", "train", "有没有快捷键列表",
    ["按 ?", "C 新建任务", "命令面板"],
    [DID["键盘快捷键速查"]], False, "easy",
    "在 Web 端按 ? 可唤出快捷键面板。常用如 C 新建任务、/ 聚焦搜索、Cmd/Ctrl+K 打开命令面板。完整列表在 设置>偏好 查看。")
add("g34", "eval", "想快点操作 有哪些键盘快捷方式",
    ["按 ?", "C 新建任务", "命令面板"],
    [DID["键盘快捷键速查"]], False, "easy",
    "按 ? 就能看到快捷键面板。比如 C 新建任务、Cmd/Ctrl+K 打开命令面板，更多在 设置>偏好 里。")

# g35 hard billing - double charge real (tribal: 预授权3-5天撤销, 超期才工单)
add("g35", "train", "我被扣了两次钱，这正常吗",
    ["设置>账单>付款方式", "3-5 个工作日内自动撤销", "超过 5 天", "保留银行流水"],
    [DID["为什么我被重复扣费了"]], False, "hard",
    "请先在 设置>账单>付款方式 检查是否有多张卡。多数重复扣费中一笔是预授权，会在 3-5 个工作日内自动撤销；若超过 5 天仍是两笔实扣，请保留银行流水并提交工单。")
add("g35", "eval", "账单上出现两笔一样的扣款怎么回事",
    ["设置>账单>付款方式", "3-5 个工作日内自动撤销", "超过 5 天", "保留银行流水"],
    [DID["为什么我被重复扣费了"]], False, "hard",
    "到 设置>账单>付款方式 看是否绑了多张卡。通常其中一笔是预授权，3-5 个工作日内自动撤销；若超过 5 天还是两笔实扣，请保留银行流水提交工单核查。")

# ============ extra ESCALATE-hard groups (raise escalate ratio) ============

# g36 hard account_security - 2FA locked out, no recovery codes -> escalate identity verify
add("g36", "train", "我手机丢了，2fa 验证码收不到，恢复码也找不到了",
    ["无法自助解除两步验证", "需人工核验身份", "转接人工安全团队"],
    [DID["开启两步验证（2FA）保护账户"]], True, "hard",
    "丢失手机且没有恢复码时无法自助解除两步验证，出于安全必须需人工核验身份。我已为您转接人工安全团队，核验您的身份后协助重置 2FA。")
add("g36", "eval", "换手机了2fa进不去 也没存恢复码 咋办",
    ["无法自助解除两步验证", "需人工核验身份", "转接人工安全团队"],
    [DID["开启两步验证（2FA）保护账户"]], True, "hard",
    "没有恢复码就无法自助解除两步验证，这种情况需人工核验身份。我已转接人工安全团队，核验身份后帮您重置 2FA。")

# g37 hard billing - suspected fraudulent charge -> escalate billing+risk
add("g37", "train", "账单里有一笔我完全没买过的扣款，是不是被盗刷了",
    ["可能涉及盗刷", "立即冻结付款方式", "转接人工账单团队核查"],
    [], True, "hard",
    "出现陌生扣款可能涉及盗刷。请立即冻结付款方式或删除相关卡片，并核对是否有他人登录。由于涉及资金风险，我已转接人工账单团队核查这笔交易。")
add("g37", "eval", "有笔莫名其妙的收费 我根本没下单 怀疑被盗刷",
    ["可能涉及盗刷", "立即冻结付款方式", "转接人工账单团队核查"],
    [], True, "hard",
    "陌生扣款可能涉及盗刷，请先立即冻结付款方式中的相关卡片。涉及资金风险，我已为您转接人工账单团队核查这笔交易。")

# g38 hard general - data residency / enterprise compliance request -> escalate
add("g38", "train", "我们要求数据必须存在欧盟境内，能配置吗",
    ["数据驻留区域选择", "需 Business 套餐", "转接销售与合规团队"],
    [DID["数据安全与合规概览"]], True, "hard",
    "数据驻留区域选择属于企业能力，需 Business 套餐才支持。具体区域配置需后台开通。由于涉及合规与套餐变更，我已为您转接销售与合规团队对接。")
add("g38", "eval", "公司合规要求数据只能放在欧盟，你们支持吗",
    ["数据驻留区域选择", "需 Business 套餐", "转接销售与合规团队"],
    [DID["数据安全与合规概览"]], True, "hard",
    "数据驻留区域选择需 Business 套餐支持，并由后台开通。涉及合规与套餐，我已转接销售与合规团队为您处理。")

# ---------------------------------------------------------------------------
# Write KB markdown + index
# ---------------------------------------------------------------------------
index_lines = []
for d in docs:
    md = "---\ndoc_id: %s\ntitle: %s\ntopic: %s\n---\n\n%s\n" % (
        d["doc_id"], d["title"], d["topic"], d["text"])
    with open(os.path.join(KB_DIR, d["doc_id"] + ".md"), "w", encoding="utf-8") as f:
        f.write(md)
    index_lines.append(json.dumps(
        {"doc_id": d["doc_id"], "title": d["title"], "topic": d["topic"], "text": d["text"]},
        ensure_ascii=False))
with open(os.path.join(KB_DIR, "index.jsonl"), "w", encoding="utf-8") as f:
    f.write("\n".join(index_lines) + "\n")

# Write queries
with open(os.path.join(EVAL_DIR, "queries.jsonl"), "w", encoding="utf-8") as f:
    for q in queries:
        f.write(json.dumps(q, ensure_ascii=False) + "\n")

print("Wrote %d KB docs, %d queries" % (len(docs), len(queries)))
