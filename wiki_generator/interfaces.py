"""What this repository exposes, and what it depends on: the contract surface.

The rest of the wiki describes the code from the inside — modules, flows, files.
This describes it from the outside: the endpoints, RPC methods, topics and
sockets through which anything that is not this repository talks to it, and the
ones it talks to in return. Both directions, because a contract has two sides
and breaking either one breaks somebody.

Detection here is deterministic and modelless. It is a grep, and it is honest
about being one: a match proves that *this line exists at this location*, and
nothing more. It does not prove the line is a route, that the path is complete
(a router mounted under a prefix has a longer one), or that the list is
exhaustive. So the signals are handed to the model as **where to look**, never
as the answer — the same treatment `<code_cartography>` gets, minus the claim to
be a verified graph.

The gate matters as much as the detection: a repository with no interfaces gets
no section, no pages and no cost. That is why detection is separate from
generation and runs first.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from .config import WikiConfig
from .i18n import translator
from .models import RepoScan
from .scanner import is_test_path
from .utils import read_text

# --- protocols ---------------------------------------------------------
HTTP = "http"
GRAPHQL = "graphql"
GRPC = "grpc"
WEBSOCKET = "websocket"
TCP = "tcp"
UDP = "udp"
QUEUE = "queue"

# --- directions --------------------------------------------------------
# EXPOSED: something outside can reach this repository through it.
# CONSUMED: this repository reaches something outside through it.
# EITHER:   the signal proves the protocol is in use but not which side of it.
EXPOSED = "exposed"
CONSUMED = "consumed"
EITHER = "either"

# One mention is a mention; two is a pattern. A `socket.socket(` used once to
# check whether a port is free does not make the repository a network service,
# and a page about it would be headings over nothing. Deliberately low all the
# same: one extra page costs little, and missing the endpoint someone was
# looking for costs the whole feature.
MIN_SIGNALS = 2

# Signals kept per page and in the prompt context. Past this the list stops
# being evidence and starts being noise the model has to wade through.
MAX_SIGNALS_IN_CONTEXT = 120
# Route literals demanded of the page. High enough to force real coverage, low
# enough that the retry prompt stays a prompt.
MAX_REQUIRED_ROUTES = 25

# Hosts that appear in source without being an integration: documentation,
# schemas, package registries, licences, and the machine itself.
NOISE_HOSTS = (
    "localhost", "127.0.0.1", "0.0.0.0", "example.com", "example.org",
    "github.com", "raw.githubusercontent.com", "gitlab.com", "w3.org",
    "www.w3.org", "schemas.xmlsoap.org", "json-schema.org", "opensource.org",
    "npmjs.com", "pypi.org", "registry.npmjs.org", "docker.io", "golang.org",
    "python.org", "nodejs.org", "mozilla.org", "developer.mozilla.org",
    "apache.org", "creativecommons.org", "gnu.org", "tools.ietf.org",
    "stackoverflow.com", "wikipedia.org", "fonts.googleapis.com",
    "fonts.gstatic.com", "unpkg.com", "cdn.jsdelivr.net", "jsdelivr.net",
)


@dataclass(frozen=True)
class Signal:
    """One located piece of evidence. `detail` is whatever the rule captured."""

    protocol: str
    direction: str
    framework: str
    rel_path: str
    line: int
    text: str
    detail: str = ""
    # Whether this match proves an interface on its own, or only that a name
    # appeared. A captured route, a declared service, a constructed client and a
    # vendor SDK are self-proving; `socket.socket(` is not.
    strong: bool = False

    @property
    def location(self) -> str:
        return f"{self.rel_path}:{self.line}"


@dataclass(frozen=True)
class SpecFile:
    """A contract written down as a file — the strongest signal there is."""

    kind: str       # openapi | asyncapi | proto | graphql | thrift | wsdl | postman
    protocol: str
    rel_path: str


@dataclass
class OutboundHost:
    host: str
    count: int = 0
    first_seen: str = ""


@dataclass
class InterfaceScan:
    signals: list[Signal] = field(default_factory=list)
    spec_files: list[SpecFile] = field(default_factory=list)
    outbound_hosts: list[OutboundHost] = field(default_factory=list)
    files_read: int = 0

    # ------------------------------------------------------------------
    def specs_for(self, *protocols: str) -> list[SpecFile]:
        wanted = set(protocols)
        return [s for s in self.spec_files if s.protocol in wanted]

    @property
    def exposed(self) -> list[Signal]:
        return [s for s in self.signals if s.direction == EXPOSED]

    @property
    def consumed(self) -> list[Signal]:
        return [s for s in self.signals if s.direction == CONSUMED]

    @property
    def protocols(self) -> list[str]:
        seen = {s.protocol for s in self.signals}
        seen |= {s.protocol for s in self.spec_files}
        return [p for p in (HTTP, GRAPHQL, GRPC, WEBSOCKET, TCP, UDP, QUEUE)
                if p in seen]

    def count_of(self, *protocols: str) -> int:
        wanted = set(protocols)
        return sum(1 for s in self.signals if s.protocol in wanted)

    def any_strong(self, *protocols: str) -> bool:
        wanted = set(protocols)
        return any(s.strong for s in self.signals if s.protocol in wanted)

    @property
    def strong_exposed(self) -> list[Signal]:
        return [s for s in self.exposed if s.strong]

    @property
    def strong_consumed(self) -> list[Signal]:
        return [s for s in self.consumed if s.strong]

    # --- which pages this repository earns -----------------------------
    @property
    def has_http(self) -> bool:
        return bool([s for s in self.strong_exposed
                     if s.protocol in (HTTP, GRAPHQL)]) or bool(
            self.specs_for(HTTP, GRAPHQL))

    @property
    def has_rpc(self) -> bool:
        return (bool(self.specs_for(GRPC)) or self.any_strong(GRPC)
                or self.count_of(GRPC) >= MIN_SIGNALS)

    @property
    def has_messaging(self) -> bool:
        return (bool(self.specs_for(QUEUE)) or self.any_strong(QUEUE)
                or self.count_of(QUEUE) >= MIN_SIGNALS)

    @property
    def has_network(self) -> bool:
        # Any raw socket at all. A Python UDP server is two lines — a `socket()`
        # and a `bind()` — and demanding a second occurrence loses exactly the
        # small protocol daemons this page exists for. When the only use really
        # is a port check, the page says so, which is cheap and true.
        return bool(self.count_of(TCP, UDP, WEBSOCKET))

    @property
    def has_consumers(self) -> bool:
        # A named external host turns "this file imports an HTTP library" into
        # "this system calls that system", so one call site is enough when a
        # host names the other end.
        return bool(self.strong_consumed) or len(self.consumed) >= MIN_SIGNALS or (
            bool(self.consumed) and bool(self.outbound_hosts)
        )

    @property
    def has_interfaces(self) -> bool:
        """Whether this repository is worth an interfaces section at all.

        Deliberately strict. A repository that merely imports `requests` once
        has no contract surface, and giving it five pages of empty headings
        costs money and teaches the reader nothing.
        """
        return (self.has_http or self.has_rpc or self.has_messaging
                or self.has_network or self.has_consumers)

    # ------------------------------------------------------------------
    def routes(self) -> list[str]:
        """Distinct route literals captured on exposed HTTP signals.

        Used as the page's coverage requirement, so only unambiguous captures
        qualify: a literal path, not a variable, a prefix constant or a regex.
        """
        found: list[str] = []
        for signal in self.signals:
            if signal.protocol != HTTP or signal.direction != EXPOSED:
                continue
            detail = signal.detail.strip()
            if not detail.startswith("/") or len(detail) < 2:
                continue
            # Interpolation and regex syntax mean the literal at that line is not
            # the path — demanding it of the page would demand something wrong.
            if any(character in detail for character in "$`^\\+*?()[]"):
                continue
            if detail not in found:
                found.append(detail)
        return found


# ----------------------------------------------------------------------
# Rules. Ordered most specific first: a line is claimed by one rule only.
# `languages` empty means "any language". `filenames` restricts a rule to files
# whose name or path contains one of the fragments, which is how the noisy but
# unavoidable patterns (Django's `path(`, Rails' `get "..."`) are made safe.
# ----------------------------------------------------------------------
@dataclass(frozen=True)
class Rule:
    protocol: str
    direction: str
    framework: str
    pattern: re.Pattern
    languages: frozenset
    filenames: tuple
    # Evidence that stands on its own. Constructing an HTTP client means this
    # repository is a client of something, however few call sites there are —
    # and a well-factored repository has exactly one, which is why counting
    # occurrences under-reports precisely the code that is easiest to document.
    strong: bool = False


# MULTILINE, because rules are matched against the whole file at once and
# several of them anchor with `^` to reach a declaration at the start of a line
# (`service X {` in a .proto, `get "/health"` in a routes.rb). Without the flag
# `^` means byte 0 of the file, so those rules match only when the declaration
# is the very first thing in it — which it never is.
FLAGS = re.MULTILINE


def _rule(protocol, direction, framework, pattern, languages=(), filenames=(),
          strong=False) -> Rule:
    return Rule(
        protocol=protocol, direction=direction, framework=framework,
        pattern=re.compile(pattern, FLAGS), languages=frozenset(languages),
        filenames=tuple(filenames), strong=strong,
    )


RULES: tuple[Rule, ...] = (
    # --- contracts declared in a schema language ----------------------
    _rule(GRPC, EXPOSED, "protobuf", r"^\s*service\s+(\w+)\s*\{", ("Protobuf",), (), True),
    _rule(GRPC, EXPOSED, "protobuf", r"^\s*rpc\s+(\w+)\s*\(", ("Protobuf",), (), True),
    _rule(GRAPHQL, EXPOSED, "graphql-schema",
          r"^\s*(?:extend\s+)?type\s+(Query|Mutation|Subscription)\b", ("GraphQL",),
          (), True),

    # --- HTTP exposed: Python -----------------------------------------
    _rule(WEBSOCKET, EXPOSED, "fastapi", r"@\w+\.websocket\(\s*[\"'](/[^\"']*)", ("Python",)),
    # The leading `/` is not cosmetic: `patch`, `get` and `post` are ordinary
    # method names, and without it `@mock.patch("urllib.request.urlopen")` is
    # read as a route (measured — it produced 4 of 4 false positives on one repo).
    _rule(HTTP, EXPOSED, "fastapi/flask",
          r"@\w+\.(?:route|get|post|put|patch|delete|head|options)\(\s*[\"'](/[^\"']*)",
          ("Python",)),
    _rule(HTTP, EXPOSED, "flask", r"\.add_url_rule\(\s*[\"'](/[^\"']*)", ("Python",), (), True),
    _rule(HTTP, EXPOSED, "django",
          r"\b(?:path|re_path)\(\s*r?[\"']([^\"']*)", ("Python",), ("urls.py",), True),
    _rule(HTTP, EXPOSED, "drf-router",
          r"\.register\(\s*r?[\"']([^\"']*)", ("Python",), ("urls.py", "routers.py"), True),
    _rule(HTTP, EXPOSED, "aiohttp",
          r"(?:web\.(?:get|post|put|delete|patch)|router\.add_\w+)\(\s*[\"'](/[^\"']*)",
          ("Python",)),
    _rule(HTTP, EXPOSED, "starlette", r"\bRoute\(\s*[\"'](/[^\"']*)", ("Python",)),

    # --- HTTP exposed: JavaScript / TypeScript ------------------------
    _rule(HTTP, EXPOSED, "nestjs",
          r"@(?:Get|Post|Put|Patch|Delete|All|Options|Head)\(\s*[\"'`]?([^\"'`)]*)",
          ("TypeScript", "JavaScript"), (), True),
    _rule(HTTP, EXPOSED, "nestjs", r"@Controller\(\s*[\"'`]?([^\"'`)]*)",
          ("TypeScript", "JavaScript"), (), True),
    # The receiver is named explicitly rather than matched as `\w+`. With any
    # receiver, `axios.get("/api/users")` reads as a route the project SERVES —
    # a fabricated endpoint, promoted to a required marker on the endpoint page,
    # which is the worst thing this feature can produce. `api` is deliberately
    # absent: in front-end code it is far more often an axios instance than a
    # router. The consumed rules below are matched first, for the same reason.
    _rule(HTTP, EXPOSED, "express/fastify/koa",
          r"\b(?:app|server|route|routes|fastify|koa|express|\w*[Rr]outer|\w*App)"
          r"\.(?:get|post|put|patch|delete|options|head|all)\("
          r"\s*[\"'`](/[^\"'`]*)",
          ("TypeScript", "JavaScript")),
    _rule(HTTP, EXPOSED, "hapi/fastify",
          r"(?:server|fastify)\.route\(\s*\{", ("TypeScript", "JavaScript")),

    # --- HTTP exposed: Go ---------------------------------------------
    _rule(HTTP, EXPOSED, "net/http-gin-chi-echo",
          r"\b\w+\.(?:GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS|Get|Post|Put|Patch"
          r"|Delete|Handle|HandleFunc)\(\s*\"(/[^\"]*)\"", ("Go",)),
    _rule(HTTP, EXPOSED, "net/http", r"http\.ListenAndServe(?:TLS)?\(\s*([^,)]*)", ("Go",)),

    # --- HTTP exposed: JVM --------------------------------------------
    _rule(HTTP, EXPOSED, "spring",
          r"@(?:Get|Post|Put|Delete|Patch|Request)Mapping\("
          r"(?:\s*(?:value|path)\s*=\s*)?\s*[\"']?([^\"')]*)",
          ("Java", "Kotlin", "Groovy"), (), True),
    _rule(HTTP, EXPOSED, "spring", r"@(?:Rest)?Controller\b", ("Java", "Kotlin"),
          (), True),
    _rule(HTTP, EXPOSED, "jax-rs", r"@Path\(\s*[\"']([^\"']*)", ("Java", "Kotlin"),
          (), True),
    _rule(HTTP, EXPOSED, "ktor",
          r"\b(?:get|post|put|delete|patch)\(\s*\"(/[^\"]*)\"\s*\)\s*\{", ("Kotlin",)),

    # --- HTTP exposed: .NET, PHP, Ruby, Rust, Elixir -------------------
    _rule(HTTP, EXPOSED, "asp.net",
          r"\[Http(?:Get|Post|Put|Delete|Patch)(?:\(\s*[\"']([^\"']*))?", ("C#",),
          (), True),
    _rule(HTTP, EXPOSED, "asp.net", r"\[Route\(\s*[\"']([^\"']*)", ("C#",), (), True),
    _rule(HTTP, EXPOSED, "minimal-api",
          r"\.Map(?:Get|Post|Put|Delete|Patch)\(\s*[\"']([^\"']*)", ("C#",)),
    _rule(HTTP, EXPOSED, "laravel/symfony",
          r"(?:Route::(?:get|post|put|patch|delete|any|match)|#\[Route|@Route)"
          r"\(\s*[\"']?([^\"',)]*)", ("PHP",), (), True),
    _rule(HTTP, EXPOSED, "rails",
          r"^\s*(?:get|post|put|patch|delete)\s+[\"']([^\"']*)", ("Ruby",),
          ("routes.rb",), True),
    _rule(HTTP, EXPOSED, "rails", r"^\s*resources?\s+:(\w+)", ("Ruby",),
          ("routes.rb",), True),
    _rule(HTTP, EXPOSED, "actix/rocket",
          r"#\[(?:get|post|put|delete|patch)\(\s*\"([^\"]*)\"", ("Rust",), (), True),
    _rule(HTTP, EXPOSED, "axum", r"\.route\(\s*\"(/[^\"]*)\"", ("Rust",)),
    _rule(HTTP, EXPOSED, "phoenix",
          r"^\s*(?:get|post|put|patch|delete)\s+\"([^\"]*)\"", ("Elixir",),
          ("router.ex",), True),

    # --- GraphQL in code ----------------------------------------------
    _rule(GRAPHQL, EXPOSED, "graphql-server",
          r"\b(?:ApolloServer|buildSchema|makeExecutableSchema|GraphQLObjectType"
          r"|graphene\.Schema|strawberry\.Schema|GraphQLSchema)\b", (), (), True),

    # --- gRPC ----------------------------------------------------------
    _rule(GRPC, EXPOSED, "grpc-server",
          r"\b(?:grpc\.NewServer|grpc\.server|ServerBuilder\.forPort"
          r"|add_\w+Servicer_to_server|Register\w+Server|tonic::transport::Server)\b",
          (), (), True),
    # `New\w+Client(` also spells `NewHTTPClient(`, so this one has to earn its
    # page by appearing more than once.
    _rule(GRPC, CONSUMED, "grpc-client",
          r"\b(?:grpc\.Dial|grpc\.NewClient|grpc\.insecure_channel"
          r"|grpc\.secure_channel|New\w+Client|\w+Stub)\("),

    # --- WebSocket ------------------------------------------------------
    _rule(WEBSOCKET, EXPOSED, "websocket-server",
          r"\b(?:WebSocketServer|WebSocket\.Server|websockets\.serve|socketio\.Server"
          r"|SocketIoServer|WebSocketHandler|upgrader\.Upgrade|melody\.New)\b",
          (), (), True),
    _rule(WEBSOCKET, CONSUMED, "websocket-client",
          r"\b(?:new WebSocket\(|websockets\.connect\(|WebSocketApp\(|socket\.io-client)",
          (), (), True),

    # --- raw TCP / UDP ---------------------------------------------------
    # Binding is the strong form: it makes the process a server. Naming a socket
    # constant is not — one `socket.socket(` can be a port availability check.
    _rule(UDP, EXPOSED, "udp-listener",
          r"\b(?:ListenUDP|dgram\.createSocket|DatagramSocket\(|UdpSocket::bind"
          r"|create_datagram_endpoint)", (), (), True),
    _rule(UDP, EITHER, "udp-socket", r"\b(?:SOCK_DGRAM|DialUDP|ResolveUDPAddr)\b"),
    _rule(TCP, EXPOSED, "tcp-listener",
          r"\b(?:net\.Listen\(|ServerSocket\(|TcpListener::bind|asyncio\.start_server"
          r"|net\.createServer\(|start_unix_server)", (), (), True),
    _rule(TCP, CONSUMED, "tcp-client",
          r"\b(?:socket\.create_connection|net\.Dial\(|TcpStream::connect"
          r"|net\.createConnection|open_connection\()"),
    _rule(TCP, EITHER, "socket",
          r"\b(?:SOCK_STREAM|socket\.socket\(|IPPROTO_TCP)\b"),

    # --- messaging: identified by library, not by method name -----------
    # `.publish(`/`.subscribe(` alone would match every RxJS call site in a
    # front end. The library name is the part that cannot mean anything else,
    # which is also what makes a single occurrence enough: a repository that
    # depends on Kafka uses Kafka, however few lines say so.
    _rule(QUEUE, EITHER, "kafka",
          r"\b(?:KafkaProducer|KafkaConsumer|@KafkaListener|kafkajs|confluent_kafka"
          r"|sarama|kafka-python|KafkaTemplate|aiokafka)\b", (), (), True),
    _rule(QUEUE, EITHER, "rabbitmq/amqp",
          r"\b(?:basic_publish|basic_consume|amqplib|amqp\.connect|pika\."
          r"|@RabbitListener|RabbitTemplate|streadway/amqp|rabbitmq)\b", (), (), True),
    _rule(QUEUE, EITHER, "sqs/sns/eventbridge",
          r"\b(?:SendMessageCommand|ReceiveMessageCommand|PublishCommand"
          r"|PutEventsCommand|client-sqs|client-sns|client-eventbridge"
          r"|sqs\.send_message|sqs\.receive_message|sns\.publish)\b", (), (), True),
    _rule(QUEUE, EITHER, "nats",
          r"\b(?:nats\.connect|nats-io|NatsConnection|jetstream)\b", (), (), True),
    _rule(QUEUE, EITHER, "mqtt",
          r"\b(?:mqtt\.connect|paho\.mqtt|MqttClient|mqtt\.Client)\b", (), (), True),
    _rule(QUEUE, EITHER, "pubsub",
          r"\b(?:google\.cloud\.pubsub|google-cloud/pubsub|azure\.servicebus"
          r"|ServiceBusClient|kinesis|put_record)\b", (), (), True),
    _rule(QUEUE, EITHER, "job-queue",
          r"\b(?:celery|bullmq|Sidekiq|ActiveJob|@nestjs/bull|rq\.Queue)\b",
          (), (), True),
    _rule(QUEUE, EITHER, "redis-stream",
          r"\b(?:xadd|xreadgroup|XADD|XREADGROUP|psubscribe)\b"),

    # --- HTTP consumed: building a client is the strong form ---------------
    _rule(HTTP, CONSUMED, "http-client",
          r"\b(?:httpx\.(?:Client|AsyncClient)\(|requests\.Session\("
          r"|aiohttp\.ClientSession\()", ("Python",), (), True),
    _rule(HTTP, CONSUMED, "requests/httpx",
          r"\b(?:requests\.(?:get|post|put|patch|delete|head|request)"
          r"|httpx\.(?:get|post|put|patch|delete)"
          r"|urllib\.request\.urlopen)\b", ("Python",)),
    _rule(HTTP, CONSUMED, "http-client",
          r"\b(?:axios\.create\(|new Agent\(|createClient\()",
          ("TypeScript", "JavaScript"), (), True),
    _rule(HTTP, CONSUMED, "axios/fetch",
          r"\b(?:axios(?:\.(?:get|post|put|patch|delete|request))?\(|got\("
          r"|superagent\.|node-fetch|ky\.(?:get|post))",
          ("TypeScript", "JavaScript", "Vue", "Svelte")),
    _rule(HTTP, CONSUMED, "fetch",
          r"\bfetch\(\s*[\"'`](https?://[^\"'`]*|/[^\"'`]*)",
          ("TypeScript", "JavaScript", "Vue", "Svelte")),
    _rule(HTTP, CONSUMED, "http-client", r"&?http\.Client\{", ("Go",), (), True),
    _rule(HTTP, CONSUMED, "net/http",
          r"\b(?:http\.(?:Get|Post|NewRequest)|client\.Do)\(", ("Go",)),
    _rule(HTTP, CONSUMED, "jvm-http",
          r"\b(?:RestTemplate|WebClient\.|HttpClient\.newHttpClient|OkHttpClient"
          r"|Retrofit\.Builder|Feign)\b", ("Java", "Kotlin"), (), True),
    _rule(HTTP, CONSUMED, "httpclient",
          r"\b(?:new HttpClient\(|IHttpClientFactory)", ("C#",), (), True),
    _rule(HTTP, CONSUMED, "httpclient", r"\.(?:GetAsync|PostAsync)\(", ("C#",)),
    _rule(HTTP, CONSUMED, "guzzle/curl",
          r"\b(?:GuzzleHttp|curl_init|curl_exec)\b", ("PHP",)),
    _rule(HTTP, CONSUMED, "ruby-http",
          r"\b(?:Net::HTTP|Faraday|HTTParty|RestClient)\b", ("Ruby",), (), True),
    _rule(HTTP, CONSUMED, "reqwest", r"\breqwest::", ("Rust",), (), True),
    _rule(HTTP, CONSUMED, "curl", r"\bcurl\s+(?:-[A-Za-z]+\s+)*https?://", ("Shell",)),
)

# Vendor SDKs. Their presence names the external system directly, which is more
# useful on the consumer page than the transport underneath it.
SDK_RULES: tuple[tuple[str, re.Pattern], ...] = tuple(
    (vendor, re.compile(pattern)) for vendor, pattern in (
        ("aws", r"\b(?:boto3|botocore|@aws-sdk/|aws-sdk|software\.amazon\.awssdk)\b"),
        ("google-cloud", r"\b(?:google\.cloud|@google-cloud/|googleapis)\b"),
        ("firebase", r"\b(?:firebase-admin|firebase/app|FirebaseApp)\b"),
        ("stripe", r"\bstripe\b"),
        ("twilio", r"\btwilio\b"),
        ("sendgrid", r"\bsendgrid\b"),
        ("openai", r"\bopenai\b"),
        ("anthropic", r"\banthropic\b"),
        ("supabase", r"\b(?:@supabase/|supabase_py|create_client)\b"),
        ("elasticsearch", r"\b(?:elasticsearch|@elastic/)\b"),
        ("sentry", r"\b(?:sentry_sdk|@sentry/)\b"),
        ("algolia", r"\balgolia\b"),
    )
)

# --- contract files, recognised by name alone --------------------------
SPEC_PATTERNS: tuple[tuple[str, str, re.Pattern], ...] = (
    ("openapi", HTTP, re.compile(
        r"(?:^|/)(?:openapi|swagger)[^/]*\.(?:ya?ml|json)$", re.I)),
    ("openapi", HTTP, re.compile(r"\.(?:openapi|swagger)\.(?:ya?ml|json)$", re.I)),
    ("asyncapi", QUEUE, re.compile(r"(?:^|/)asyncapi[^/]*\.(?:ya?ml|json)$", re.I)),
    ("proto", GRPC, re.compile(r"\.proto$", re.I)),
    ("graphql", GRAPHQL, re.compile(r"\.(?:graphql|gql)$", re.I)),
    ("thrift", GRPC, re.compile(r"\.thrift$", re.I)),
    ("wsdl", HTTP, re.compile(r"\.wsdl$", re.I)),
    ("raml", HTTP, re.compile(r"\.raml$", re.I)),
    ("postman", HTTP, re.compile(r"postman_collection\.json$", re.I)),
    ("avro", QUEUE, re.compile(r"\.avsc$", re.I)),
)

# Routes that exist because of where a file sits, not because of what it says.
PATH_RULES: tuple[tuple[str, str, re.Pattern], ...] = (
    ("next-pages-api", HTTP, re.compile(r"(?:^|/)pages/api/.+\.(?:t|j)sx?$")),
    ("next-app-router", HTTP, re.compile(r"(?:^|/)app/.+/route\.(?:t|j)sx?$")),
    ("sveltekit", HTTP, re.compile(r"(?:^|/)routes/.+/\+server\.(?:t|j)s$")),
    ("nuxt-server", HTTP, re.compile(r"(?:^|/)server/api/.+\.(?:t|j)s$")),
)

URL_PATTERN = re.compile(r"https?://([A-Za-z0-9._-]+\.[A-Za-z]{2,})")

# The languages worth reading. Everything else is data, markup or prose, and a
# route declaration does not live there.
READABLE_LANGUAGES = frozenset({
    "Python", "TypeScript", "JavaScript", "Go", "Rust", "Java", "Kotlin", "Ruby",
    "PHP", "C#", "F#", "C", "C++", "Swift", "Scala", "Clojure", "Elixir",
    "Erlang", "Lua", "Dart", "Shell", "Vue", "Svelte", "Protobuf", "GraphQL",
})

# Configuration that declares interfaces rather than implementing them.
READABLE_NAMES = frozenset({
    "serverless.yml", "serverless.yaml", "template.yaml", "template.yml",
    "docker-compose.yml", "docker-compose.yaml", "compose.yaml", "nginx.conf",
})


# ----------------------------------------------------------------------
_RULE_CACHE: dict[tuple[str, tuple], tuple[list[Rule], re.Pattern | None]] = {}


def _rules_for(language: str, rel_path: str) -> tuple[list[Rule], re.Pattern | None]:
    """The rules that can apply here, plus their union.

    Running fifty regexes over every file is fifty passes over the text, and on
    a repository of thousands of files that is minutes. Almost no file contains
    any interface at all, so one pass with the union answers "is there anything
    here" and the fifty only run when there is. The union is exact: if it does
    not match, no member can.
    """
    name = rel_path.rsplit("/", 1)[-1]
    scoped = tuple(
        sorted(
            fragment
            for rule in RULES
            for fragment in rule.filenames
            if fragment in name or fragment in rel_path
        )
    )
    key = (language, scoped)
    cached = _RULE_CACHE.get(key)
    if cached is not None:
        return cached

    applicable = []
    for rule in RULES:
        if rule.languages and language not in rule.languages:
            continue
        if rule.filenames and not any(fragment in scoped for fragment in rule.filenames):
            continue
        applicable.append(rule)
    # Same flags as the members, or the union rejects a file that a member
    # would have matched and the rules never run at all.
    union = (
        re.compile("|".join(f"(?:{rule.pattern.pattern})" for rule in applicable),
                   FLAGS)
        if applicable else None
    )
    _RULE_CACHE[key] = (applicable, union)
    return applicable, union


def _line_of(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def _is_noise_host(host: str) -> bool:
    """`www.apache.org` is `apache.org`, and `evil.example.com` is not the point."""
    return host.endswith(".local") or any(
        host == noise or host.endswith("." + noise) for noise in NOISE_HOSTS
    )


def detect(scan: RepoScan, config: WikiConfig) -> InterfaceScan:
    """Find the contract surface. Reads files; calls no model.

    Files are read once and matched with only the rules that can apply to their
    language, which is what keeps this affordable on a repository of thousands
    of files.
    """
    result = InterfaceScan()
    repo = Path(scan.root)
    hosts: dict[str, OutboundHost] = {}
    claimed: set[tuple[str, int]] = set()

    # Tests are excluded on purpose. They are full of the same tokens — mocked
    # clients, fixture URLs, a patched `urlopen` — and what they prove is that
    # the test file mentions an interface, not that the system has one.
    documented = {f.rel_path for f in scan.source_files}

    for info in scan.files:
        rel = info.rel_path
        # A fixture repository under `tests/` carries a real-looking
        # `openapi.yaml` and `.proto` of its own. Reading those as this
        # project's contract documents the fixture (measured: 2 of 2 spec files
        # found across two real repositories were test fixtures).
        if is_test_path(rel):
            continue
        for kind, protocol, pattern in SPEC_PATTERNS:
            if pattern.search(rel):
                result.spec_files.append(
                    SpecFile(kind=kind, protocol=protocol, rel_path=rel)
                )
                break
        for framework, protocol, pattern in PATH_RULES:
            if pattern.search(rel):
                result.signals.append(Signal(
                    protocol=protocol, direction=EXPOSED, framework=framework,
                    rel_path=rel, line=1, text=f"file-based route ({framework})",
                    detail="", strong=True,
                ))
                break

    for info in scan.files:
        name = info.rel_path.rsplit("/", 1)[-1]
        if is_test_path(info.rel_path):
            continue
        if info.rel_path not in documented and name not in READABLE_NAMES:
            continue
        if info.language not in READABLE_LANGUAGES and name not in READABLE_NAMES:
            continue
        text = read_text(repo / info.rel_path, max_chars=config.max_file_size_bytes)
        if not text:
            continue
        result.files_read += 1
        docstring_end = module_docstring_end(text)

        rules, union = _rules_for(info.language, info.rel_path)
        for rule in (rules if union is not None and union.search(text) else ()):
            for match in rule.pattern.finditer(text):
                line = _line_of(text, match.start())
                if (info.rel_path, line) in claimed:
                    continue
                claimed.add((info.rel_path, line))
                if _is_prose(text, match.start(), docstring_end):
                    continue
                detail = ""
                if match.groups():
                    # Collapsed and capped: a capture runs across lines when an
                    # annotation is written multi-line, and a newline inside a
                    # cell ends the Markdown table early.
                    detail = " ".join((match.group(1) or "").split())
                    detail = detail.strip("\"'` ")[:120]
                result.signals.append(Signal(
                    protocol=rule.protocol, direction=rule.direction,
                    framework=rule.framework, rel_path=info.rel_path, line=line,
                    text=_snippet(text, match.start()), detail=detail,
                    strong=rule.strong or detail.startswith("/"),
                ))

        for vendor, pattern in SDK_RULES:
            match = next(
                (m for m in pattern.finditer(text)
                 if not _is_prose(text, m.start(), docstring_end)),
                None,
            )
            if match is None:
                continue
            line = _line_of(text, match.start())
            result.signals.append(Signal(
                protocol=HTTP, direction=CONSUMED, framework=f"sdk:{vendor}",
                rel_path=info.rel_path, line=line,
                text=_snippet(text, match.start()), detail=vendor, strong=True,
            ))

        for match in URL_PATTERN.finditer(text):
            host = match.group(1).lower()
            # Same guard as the rules: a URL in a comment is a link someone left
            # for a reader, not a system this code calls.
            if _is_noise_host(host) or _is_prose(text, match.start(), docstring_end):
                continue
            entry = hosts.get(host)
            if entry is None:
                hosts[host] = OutboundHost(
                    host=host, count=1,
                    first_seen=f"{info.rel_path}:{_line_of(text, match.start())}",
                )
            else:
                entry.count += 1

    result.signals.sort(key=lambda s: (s.protocol, s.direction, s.rel_path, s.line))
    result.spec_files.sort(key=lambda s: s.rel_path)
    result.outbound_hosts = sorted(
        hosts.values(), key=lambda h: (-h.count, h.host)
    )[:40]
    return result


def cheap_hint(scan: RepoScan) -> str:
    """Whether this repository looks API-first, judged from filenames alone.

    Costs nothing — the scan already holds the listing — so it can run on every
    repository of a thousand-repository sweep. Only contract files qualify: a
    `.proto` or an `openapi.yaml` means the interface is the point of the
    project, and generating its wiki without documenting that would be an odd
    thing to have done silently.
    """
    found = [
        info.rel_path for info in scan.files
        if not is_test_path(info.rel_path)
        and any(pattern.search(info.rel_path) for _, _, pattern in SPEC_PATTERNS)
    ]
    if not found:
        return ""
    shown = ", ".join(found[:3]) + (f" (+{len(found) - 3})" if len(found) > 3 else "")
    return (
        f"{len(found)} interface contract file(s) here ({shown}). "
        "`--interfaces` documents them as a reference section; without it they "
        "are only mentioned in passing on the integrations page."
    )


def _snippet(text: str, offset: int, limit: int = 120) -> str:
    flat = " ".join(_line_at(text, offset).split())
    return flat[:limit] + ("..." if len(flat) > limit else "")


def _line_at(text: str, offset: int) -> str:
    start = text.rfind("\n", 0, offset) + 1
    end = text.find("\n", offset)
    return text[start:end if end != -1 else len(text)]


# Comment openers across the languages this scans. A route named in a comment is
# a comment about a route: it is as likely to be describing what was removed, or
# what the reader should not do, as what the code serves.
COMMENT_PREFIXES = ("#", "//", "*", "/*", "--", '"""', "'''", ";", "%")


def module_docstring_end(text: str) -> int:
    """Where a leading module docstring stops, or 0 if the file has none.

    A bounded region, deliberately. Counting triple-quote parity across the
    whole file instead — which is what this did first — means one triple quote
    inside an ordinary string literal flips the parity for everything after it
    and silently discards every remaining match in that file. Measured: a single
    `DOCSTRING_OPENER = '\"\"\"'` above the routes removed every endpoint of an
    HTTP service and the section with them. Trading one false positive for a
    whole API is not a trade.
    """
    stripped = text.lstrip()
    if not stripped.startswith(('"""', "'''")):
        return 0
    quote = stripped[:3]
    start = len(text) - len(stripped)
    end = text.find(quote, start + 3)
    return len(text) if end == -1 else end + 3


def _is_prose(text: str, offset: int, docstring_end: int = 0) -> bool:
    """Whether this match sits in a comment or a docstring rather than in code.

    The line prefix catches ordinary comments. The docstring bound catches the
    case that actually cost a false positive here: a module docstring whose
    continuation lines start with a plain word, so the usage example
    ``celery -A app.tasks worker`` inside it reads as evidence of a Celery worker.
    """
    if offset < docstring_end:
        return True
    return _line_at(text, offset).lstrip().startswith(COMMENT_PREFIXES)


# ----------------------------------------------------------------------
# The context block handed to the model.
# ----------------------------------------------------------------------
def context_block(iscan: InterfaceScan, protocols: tuple[str, ...] = (),
                  directions: tuple[str, ...] = ()) -> str:
    """Evidence for one page: located, unranked, and explicitly not conclusions."""
    signals = iscan.signals
    if protocols:
        signals = [s for s in signals if s.protocol in protocols]
    if directions:
        signals = [s for s in signals if s.direction in directions or s.direction == EITHER]
    specs = (iscan.specs_for(*protocols) if protocols else iscan.spec_files)
    if not signals and not specs:
        return ""

    total = len(signals)
    shown = signals[:MAX_SIGNALS_IN_CONTEXT]
    lines = [
        f"- `{s.rel_path}:{s.line}` [{s.protocol}/{s.direction}] {s.framework}"
        + (f" -> `{s.detail}`" if s.detail else "")
        + f"  |  {s.text}"
        for s in shown
    ]
    more = (f"\n... and {total - len(shown)} more matches not listed here."
            if total > len(shown) else "")
    count = f"{total} total" + (f", {len(shown)} shown" if more else "")
    spec_lines = "\n".join(f"- `{s.rel_path}` ({s.kind})" for s in specs) or "- (none)"

    return f"""<detected_interfaces>
These are TEXT MATCHES found by a static scan, not a verified contract. Each line
below is a real location in the repository — the file and line exist and contain
what is quoted. Nothing else about them is established:

- the match may not be an interface at all (a helper named `get`, a comment);
- a captured path is the literal written at that line, NOT the final route: the
  router's mount prefix is usually declared somewhere else and must be found;
- the list is INCOMPLETE by construction. Frameworks this scan does not know,
  routes built at runtime and generated code are all missing from it.

Use it as a starting index: open each location, and search for what it missed.
Never copy a line from here into the page without having read the file.

Contract files in the repository (these ARE authoritative — read them):
{spec_lines}

Matches ({count}):
{chr(10).join(lines)}{more}
</detected_interfaces>
"""


# ----------------------------------------------------------------------
# The deterministic inventory page.
# ----------------------------------------------------------------------
INVENTORY_PATH = "08-interfaces/inventory.md"
SIDECAR_PATH = "08-interfaces/interfaces.json"

PROTOCOL_LABELS = {
    HTTP: "HTTP", GRAPHQL: "GraphQL", GRPC: "gRPC", WEBSOCKET: "WebSocket",
    TCP: "TCP", UDP: "UDP", QUEUE: "Messaging",
}


SECTION_DIR = "08-interfaces"


def clear_artifacts(output_path: Path) -> None:
    """Remove the whole section. Used when it is no longer being generated.

    A stale endpoint reference is worse than a stale prose page: prose that has
    drifted reads as vague, while a route table that no longer matches the code
    reads as authoritative and gets built against.
    """
    import shutil

    shutil.rmtree(Path(output_path) / SECTION_DIR, ignore_errors=True)


def prune(output_path: Path, keep: set[str]) -> list[str]:
    """Drop pages of this section that the current plan no longer contains.

    A repository that stops using Kafka should stop having a messaging contract
    page, and nothing else removes it: the page cache only tracks fingerprints,
    and a page that is no longer planned is never visited again.
    """
    directory = Path(output_path) / SECTION_DIR
    if not directory.is_dir():
        return []
    removed = []
    for path in sorted(directory.glob("*.md")):
        rel = f"{SECTION_DIR}/{path.name}"
        if rel in keep:
            continue
        try:
            path.unlink()
        except OSError:
            continue
        removed.append(rel)
    return removed


def _table(header: list[str], rows: list[list[str]]) -> list[str]:
    lines = ["| " + " | ".join(header) + " |",
             "|" + "|".join("---" for _ in header) + "|"]
    lines += ["| " + " | ".join(cell.replace("|", "\\|") for cell in row) + " |"
              for row in rows]
    return lines


def _diagram(iscan: InterfaceScan, project: str, t) -> list[str]:
    """Who reaches in, who this reaches out to, and over what."""
    inbound = sorted({s.protocol for s in iscan.signals if s.direction == EXPOSED}
                     | {s.protocol for s in iscan.spec_files})
    outbound = sorted({s.protocol for s in iscan.signals if s.direction == CONSUMED})
    # A protocol whose side could not be determined gets a bidirectional edge
    # rather than being dropped. Leaving Kafka out of the map because the scan
    # could not tell producing from consuming hides the protocol entirely, which
    # is a worse answer than an arrow that points both ways.
    either = sorted({s.protocol for s in iscan.signals if s.direction == EITHER}
                    - set(inbound) - set(outbound))
    if not inbound and not outbound and not either:
        return []
    node = re.sub(r"[^A-Za-z0-9]", "_", project) or "system"
    # The label is quoted in Mermaid, so a quote in the project name closes it
    # early and the whole diagram fails to render — taking the page with it.
    label = project.replace('"', "'").replace("[", "(").replace("]", ")")
    lines = ["```mermaid", "flowchart LR", f'  {node}["{label}"]']
    for protocol in inbound:
        lines.append(
            f'  in_{protocol}["{t("iface.d.callers")}"] '
            f'-->|{PROTOCOL_LABELS.get(protocol, protocol)}| {node}'
        )
    for protocol in outbound:
        lines.append(
            f'  {node} -->|{PROTOCOL_LABELS.get(protocol, protocol)}| '
            f'out_{protocol}["{t("iface.d.external")}"]'
        )
    for protocol in either:
        lines.append(
            f'  {node} <-->|{PROTOCOL_LABELS.get(protocol, protocol)}| '
            f'both_{protocol}["{t("iface.d.external")}"]'
        )
    lines.append("```")
    return lines


def write_inventory(iscan: InterfaceScan, config: WikiConfig) -> list[Path]:
    """The catalogue, computed rather than written. Free, exact, and limited."""
    t = translator(config.language)
    project = config.resolved_project_name
    lines = [
        f"# {t('iface.inventory.title')}",
        "",
        t("iface.inventory.intro"),
        "",
        *_table([t("iface.th.metric"), t("iface.th.value")], [
            [t("iface.m.exposed"), str(len(iscan.exposed))],
            [t("iface.m.consumed"), str(len(iscan.consumed))],
            [t("iface.m.protocols"),
             ", ".join(PROTOCOL_LABELS.get(p, p) for p in iscan.protocols) or "-"],
            [t("iface.m.specs"), str(len(iscan.spec_files))],
            [t("iface.m.hosts"), str(len(iscan.outbound_hosts))],
            [t("iface.m.scanned"), str(iscan.files_read)],
        ]),
        "",
    ]

    diagram = _diagram(iscan, project, t)
    if diagram:
        lines += [f"## {t('iface.h.map')}", "", *diagram, ""]

    if iscan.spec_files:
        lines += [
            f"## {t('iface.h.specs')}", "", t("iface.specs.intro"), "",
            *_table([t("iface.th.file"), t("iface.th.kind"), t("iface.th.protocol")],
                    [[f"`{s.rel_path}`", s.kind,
                      PROTOCOL_LABELS.get(s.protocol, s.protocol)]
                     for s in iscan.spec_files[:80]]),
            "",
        ]

    for heading, signals in (
        (t("iface.h.exposed"), iscan.exposed),
        (t("iface.h.consumed"), iscan.consumed),
        (t("iface.h.either"), [s for s in iscan.signals if s.direction == EITHER]),
    ):
        if not signals:
            continue
        rows = [[PROTOCOL_LABELS.get(s.protocol, s.protocol), s.framework,
                 f"`{s.detail}`" if s.detail else "-", f"`{s.location}`"]
                for s in signals[:200]]
        lines += [
            f"## {heading}", "",
            *_table([t("iface.th.protocol"), t("iface.th.framework"),
                     t("iface.th.detail"), t("iface.th.location")], rows),
        ]
        if len(signals) > 200:
            lines.append("")
            lines.append(t("iface.more", count=len(signals) - 200))
        lines.append("")

    if iscan.outbound_hosts:
        lines += [
            f"## {t('iface.h.hosts')}", "", t("iface.hosts.intro"), "",
            *_table([t("iface.th.host"), t("iface.th.occurrences"),
                     t("iface.th.location")],
                    [[f"`{h.host}`", str(h.count), f"`{h.first_seen}`"]
                     for h in iscan.outbound_hosts]),
            "",
        ]

    lines += ["---", "", t("iface.footer.deterministic"), ""]

    target = config.output_path / INVENTORY_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")

    sidecar = config.output_path / SIDECAR_PATH
    sidecar.write_text(
        json.dumps(
            {
                "project": project,
                "protocols": iscan.protocols,
                "spec_files": [
                    {"path": s.rel_path, "kind": s.kind, "protocol": s.protocol}
                    for s in iscan.spec_files
                ],
                "signals": [
                    {
                        "protocol": s.protocol, "direction": s.direction,
                        "framework": s.framework, "location": s.location,
                        "detail": s.detail, "text": s.text,
                    }
                    for s in iscan.signals
                ],
                "outbound_hosts": [
                    {"host": h.host, "occurrences": h.count, "first_seen": h.first_seen}
                    for h in iscan.outbound_hosts
                ],
            },
            indent=2, ensure_ascii=False,
        ) + "\n",
        encoding="utf-8",
    )
    return [target, sidecar]
