#!groovy
// Jenkins Markup Formatter — Phase R-MVP TR.10
//
// Jenkins 의 잡 description / 빌드 description / agent description 등에서
// HTML(특히 <a> 태그·이모지) 을 안전하게 렌더링하기 위해 OWASP Java HTML
// Sanitizer 기반의 "Safe HTML" formatter 를 채택한다.
//
// 동작:
//   - <a href> / <p> / <code> / <em> / <strong> / 이모지 통과
//   - <script> / on* 핸들러 / javascript: URL 차단
//
// 활용:
//   - provision.sh 가 createItem 으로 ZeroTouch-QA 잡을 만들 때 description 을
//     HTML 로 박는다 (Recording UI 링크 포함). markup formatter 가 plain text
//     였다면 a 태그가 escape 되어 클릭 불가했을 것.
//
// 의존성:
//   - antisamy-markup-formatter plugin (Jenkins 에 포함된 표준 plugin).
//     image (Dockerfile.allinone) 의 jenkins-plugins/ 에 이미 포함되어 있다고
//     가정 — 미포함 시 본 init 은 silently skip.

import hudson.markup.RawHtmlMarkupFormatter
import jenkins.model.Jenkins

def instance = Jenkins.get()

try {
  // antisamy-markup-formatter plugin 의 RawHtmlMarkupFormatter 를 시도.
  // Class 미존재 (= plugin 누락) 시 ClassNotFoundException → catch.
  def cls = Class.forName("hudson.markup.RawHtmlMarkupFormatter")
  // disableSyntaxHighlighting=false. allowedTags 는 plugin default (Safe HTML).
  def formatter = cls.getDeclaredConstructor(Boolean.TYPE).newInstance(false)
  instance.setMarkupFormatter(formatter)
  instance.save()
  println "[init.groovy.d] Markup Formatter → RawHtmlMarkupFormatter (Safe HTML)"
} catch (ClassNotFoundException e) {
  println "[init.groovy.d] antisamy-markup-formatter plugin 미설치 — Markup Formatter 변경 skip"
} catch (Throwable t) {
  println "[init.groovy.d] Markup Formatter 설정 실패: ${t.message}"
}
