# Публичный API

Этот документ описывает публичный Python API пакета `etcd_fsm` и команду
`etcd-fsm graph`.

Текущая версия библиотеки предоставляет:

- неизменяемые модели состояний и команд;
- декларацию общей FSM-схемы;
- процессоры с переходами, выводимыми из сигнатур функций;
- проверку и композицию графа переходов;
- CLI для вывода графа в Mermaid или Graphviz DOT.

Production runtime для исполнения процессоров поверх etcd еще разрабатывается
и в публичный API не входит.

Все поддерживаемые Python-импорты доступны непосредственно из `etcd_fsm`:

```python
from etcd_fsm import (
    __version__,
    Applied,
    BaseState,
    Command,
    Delete,
    FSMProcessor,
    FSMSchema,
    FrozenModel,
    Ignored,
    MatchMode,
    Rejected,
    Retry,
    compose_processors,
)
```

Текущая версия дистрибутива доступна как `etcd_fsm.__version__`. Ее
единственный источник находится в `etcd_fsm/version.py`; значение версии в
`pyproject.toml` объявлено динамическим.

## Минимальный пример

```python
from enum import StrEnum, auto
from typing import Literal
from uuid import UUID

from etcd_fsm import (
    Applied,
    BaseState,
    Command,
    FSMProcessor,
    FSMSchema,
    Retry,
)


class OrderStatus(StrEnum):
    CREATED = auto()
    PAYMENT_REQUIRED = auto()
    PAID = auto()


class OrderCommand(StrEnum):
    CREATE = auto()


class OrderState[S: OrderStatus](BaseState[S]):
    order_id: UUID

    def fsm_key(self) -> str:
        return str(self.order_id)


class Created(OrderState[Literal[OrderStatus.CREATED]]):
    state: Literal[OrderStatus.CREATED] = OrderStatus.CREATED


class PaymentRequired(OrderState[Literal[OrderStatus.PAYMENT_REQUIRED]]):
    state: Literal[OrderStatus.PAYMENT_REQUIRED] = OrderStatus.PAYMENT_REQUIRED
    attempt: int = 1


class Paid(OrderState[Literal[OrderStatus.PAID]]):
    state: Literal[OrderStatus.PAID] = OrderStatus.PAID
    payment_id: UUID


type CreateOrderResult = Applied[Created]


class CreateOrder(Command[CreateOrderResult]):
    command: Literal[OrderCommand.CREATE] = OrderCommand.CREATE
    order_id: UUID

    def fsm_key(self) -> str:
        return str(self.order_id)


orders = FSMSchema(
    name="orders",
    namespace="/state/orders",
    state_enum=OrderStatus,
    states=(Created, PaymentRequired, Paid),
    command_enum=OrderCommand,
    commands=(CreateOrder,),
)

payments = FSMProcessor(schema=orders, name="payments")


@payments.initializer()
async def create_order(command: CreateOrder) -> CreateOrderResult | Retry:
    return Applied(Created(order_id=command.order_id))


@payments.reaction()
async def request_payment(state: Created) -> PaymentRequired:
    return PaymentRequired(order_id=state.order_id)


@payments.reaction()
async def charge_payment(state: PaymentRequired) -> Paid | Retry:
    return Retry()


payments.finalize()
```

Аннотации обработчиков являются единственным источником графа переходов.
Например, `Created -> PaymentRequired` выводится из параметра и возвращаемого
типа `request_payment`.

## Модели

### `FrozenModel`

Базовая Pydantic-модель для неизменяемых пользовательских данных:

```python
class FrozenModel(BaseModel): ...
```

Ее конфигурация:

- запрещает неизвестные поля (`extra="forbid"`);
- запрещает изменение созданного объекта (`frozen=True`);
- повторно валидирует переданные экземпляры (`revalidate_instances="always"`).

`FrozenModel` удобно использовать для типизированных ошибок доменного уровня:

```python
class CannotCancelOrder(FrozenModel):
    current_state: OrderStatus
```

### `BaseState[S]`

Абстрактная неизменяемая Pydantic-модель одного состояния:

```python
class BaseState[S: Enum](FrozenModel, ABC):
    state: S

    def fsm_key(self) -> str: ...
```

Обычно сначала определяется общий родитель домена:

```python
class OrderState[S: OrderStatus](BaseState[S]):
    account_id: UUID
    order_id: UUID

    def fsm_key(self) -> str:
        return f"{self.account_id}/{self.order_id}"
```

Каждая конкретная модель обязана локально объявить ровно один discriminator:

```python
class Created(OrderState[Literal[OrderStatus.CREATED]]):
    state: Literal[OrderStatus.CREATED] = OrderStatus.CREATED
    total: Decimal
```

Для поля `state` обязательны одновременно:

- аннотация `Literal[КонкретныйEnumMember]`;
- ровно один элемент внутри `Literal`;
- default, идентичный этому enum member;
- enum того же класса, который передан в `FSMSchema.state_enum`.

Состояния являются snapshots. Изменение состояния или его данных создается как
новый объект:

```python
next_state = PaymentRequired(
    account_id=state.account_id,
    order_id=state.order_id,
    total=state.total,
    attempt=1,
)
```

### `Command[R]`

Абстрактная неизменяемая Pydantic-модель команды:

```python
class Command[R](FrozenModel, ABC):
    def fsm_key(self) -> str: ...
```

Generic-параметр `R` задает полный тип результата команды:

```python
type CancelOrderResult = (
    Applied[Cancelled]
    | Rejected[CannotCancelOrder]
    | Ignored
)


class CancelOrder(Command[CancelOrderResult]):
    command: Literal[OrderCommand.CANCEL] = OrderCommand.CANCEL
    account_id: UUID
    order_id: UUID
    reason: str

    def fsm_key(self) -> str:
        return f"{self.account_id}/{self.order_id}"
```

Конкретная Command Model обязана:

- специализировать `Command[ResultT]` без неразрешенного `Any`;
- локально объявить поле `command` как `Literal` одного enum member;
- задать этому полю соответствующий default;
- вернуть из `fsm_key()` ключ адресуемого State Value.

### `FSMKey`

Runtime-checkable протокол объекта с методом:

```python
class FSMKey(Protocol):
    def fsm_key(self) -> str: ...
```

`BaseState` и `Command` реализуют этот контракт абстрактно. Пользовательские
модели обязаны предоставить синхронный метод без дополнительных параметров с
аннотацией возвращаемого типа `str`.

### `validate_fsm_key`

```python
def validate_fsm_key(key: object) -> str: ...
```

Проверяет и возвращает ключ без изменения. State Key является относительным
нормализованным путем и может быть составным:

```python
validate_fsm_key("account-id/order-id")
```

Запрещены:

- нестроковые и пустые значения;
- NUL;
- пустые path segments, включая начальный или завершающий `/`;
- segments `.`, `..` и зарезервированный `rpc`.

При ошибке выбрасывается `InvalidFSMKey`.

## Результаты обработчиков

Все result-типы являются неизменяемыми dataclass-объектами с полем `kind`.

| Тип | Создание | Значение `kind` | Назначение |
| --- | --- | --- | --- |
| `Applied[S]` | `Applied(next_state)` | `"applied"` | Команда успешно создала новое состояние |
| `Rejected[E]` | `Rejected(error)` | `"rejected"` | Ожидаемый типизированный отказ |
| `Ignored` | `Ignored()` | `"ignored"` | Команда корректна, но ничего менять не нужно |
| `Delete` | `Delete()` | `"delete"` | Удалить State Value |
| `Retry` | `Retry()` | `"retry"` | Повторить обработку |

### `Applied[S]`

Содержит поле `state: S`. Используется только в результатах Command Handler и
Initializer. Reaction возвращает State Value напрямую, без `Applied`.

### `Rejected[E]`

Содержит поле `error: E`. Тип ошибки обязан быть Pydantic-моделью. Это обычный
доменный результат, а не исключение:

```python
return Rejected(CannotCancelOrder(current_state=state.state))
```

### `Ignored`

Завершает Command Call без изменения State Value:

```python
return Ignored()
```

### `Delete`

Запрашивает удаление текущего State Value:

```python
return Delete()
```

`Delete` может входить в результат Command Handler или Reaction. Initializer не
может возвращать `Delete`.

### `Retry`

Просит runtime повторить текущую операцию:

```python
return Retry()
return Retry(after=timedelta(seconds=5))
```

`after` имеет тип `timedelta | None` и не может быть отрицательным. `Retry`
может дополнительно входить в return annotation Reaction, Command Handler или
Initializer.

## `FSMSchema`

```python
class FSMSchema[S: Enum]:
    def __init__(
        self,
        *,
        name: str,
        namespace: str,
        state_enum: type[S],
        states: Iterable[type[BaseState[Any]]],
        command_enum: type[Enum] | None = None,
        commands: Iterable[type[Command[Any]]] = (),
    ) -> None: ...
```

FSM Schema является общим, не связанным с подключением к etcd описанием домена.
Она должна быть одинаковой для независимо развернутых процессоров.

### Параметры

- `name`: стабильное непустое имя без `/`.
- `namespace`: абсолютный нормализованный etcd path. Он должен начинаться с
  `/`, не может быть корнем, оканчиваться на `/`, содержать NUL, пустые
  segments, `.` или `..`.
- `state_enum`: Enum-класс discriminator-ов состояний. Enum aliases запрещены.
- `states`: полный набор конкретных State Models. Каждый member `state_enum`
  должен быть представлен ровно одной моделью.
- `command_enum`: необязательный Enum-класс discriminator-ов команд. Enum
  aliases запрещены.
- `commands`: полный набор Command Models. Если он непустой, `command_enum`
  обязателен. Каждый member `command_enum` должен быть представлен ровно одной
  моделью.

### Атрибуты и методы

```python
schema.name: str
schema.namespace: str
schema.state_enum: type[Enum]
schema.command_enum: type[Enum] | None

schema.states: Mapping[Enum, type[BaseState[Any]]]
schema.commands: Mapping[Enum, CommandDefinition]
```

Обе mapping являются read-only.

```python
schema.state_model(discriminator)
```

Возвращает конкретную State Model для discriminator.

```python
schema.command_definition(CommandModel)
```

Возвращает `CommandDefinition` зарегистрированной модели.

```python
schema.contains_state_model(model)
schema.contains_command_model(model)
```

Проверяют регистрацию по identity класса, а не через `issubclass`.

```python
schema.validate_state_value(value)
schema.validate_command_value(value)
```

Повторно валидируют объект соответствующей конкретной моделью, проверяют enum,
соответствие discriminator-а классу и результат `fsm_key()`. Возвращают
перевалидированный объект. При нарушении контракта выбрасывается
`SchemaDefinitionError` или `InvalidFSMKey`.

### `CommandDefinition`

Read-only dataclass с метаданными зарегистрированной команды:

```python
@dataclass(frozen=True, slots=True)
class CommandDefinition:
    model: type[Command[Any]]
    discriminator: Enum
    result_type: Any
```

`result_type` содержит разрешенную аннотацию из `Command[ResultT]`.

## `FSMProcessor`

```python
class FSMProcessor:
    def __init__(self, *, schema: FSMSchema[Any], name: str) -> None: ...
```

FSM Processor группирует функции одного независимо развертываемого сервиса.
Он не хранит текущее состояние и не владеет подключением к etcd.

`name` должен быть стабильной непустой строкой без `/`.

### Общие правила функций

Все функции, зарегистрированные через процессор:

- объявляются через `async def`;
- полностью аннотируют параметры и return;
- не используют `Any`, `*args` или `**kwargs`;
- ссылаются только на модели своей `FSMSchema`;
- сохраняются декоратором без обертки: декоратор возвращает исходную функцию.

По умолчанию имя определения равно
`"{function.__module__}.{function.__qualname__}"`. Параметр `name=` задает
стабильное имя явно. Имена внутри процессора должны быть уникальны и не могут
содержать `/`.

Декораторы поддерживают обе формы:

```python
@processor.reaction
async def first(...): ...


@processor.reaction()
async def second(...): ...
```

### `reaction`

```python
processor.reaction(
    function=None,
    *,
    match=MatchMode.EXACT,
    name=None,
)
```

Reaction принимает ровно одну State annotation:

```python
@payments.reaction()
async def charge(state: PaymentRequired) -> Paid | Retry:
    ...
```

Допустимые элементы return union:

- зарегистрированная конкретная State Model: выполнить переход;
- `Delete`: удалить текущее состояние;
- `Retry`: повторить Reaction;
- `None`: успешно завершить без изменения State Value.

Observer Reaction не содержит State Model или `Delete` в return annotation:

```python
@audit.reaction()
async def record(state: Paid) -> Retry | None:
    ...
```

Для одной конкретной State Model может существовать только одна переходящая
Reaction в составленном графе. Observer Reactions не ограничены этим правилом.

### `command`

```python
processor.command(
    function=None,
    *,
    match=MatchMode.EXACT,
    name=None,
)
```

Command Handler принимает ровно одну зарегистрированную Command Model и одну
State annotation. Порядок этих двух параметров не имеет значения:

```python
@orders_processor.command()
async def cancel(
    command: CancelOrder,
    state: Created | PaymentRequired,
) -> CancelOrderResult | Retry:
    ...
```

Return annotation без `Retry` должна в точности совпадать с `ResultT`,
объявленным моделью `Command[ResultT]`. Добавить `Retry` можно, убрать или
добавить другой completed outcome нельзя.

Одна Command Model может иметь только одного владельца среди скомпонованных
процессоров.

### `initializer`

```python
processor.initializer(
    function=None,
    *,
    name=None,
)
```

Initializer обрабатывает команду для еще не существующего State Value и
принимает ровно одну зарегистрированную Command Model:

```python
@orders_processor.initializer()
async def create(command: CreateOrder) -> CreateOrderResult | Retry:
    ...
```

Его result type подчиняется тем же правилам, что Command Handler, но обязан
содержать хотя бы один `Applied[StateModel]` и не может содержать `Delete`.

### `MatchMode`

```python
class MatchMode(StrEnum):
    EXACT = "exact"
    SUBCLASSES = "subclasses"
```

- `EXACT`: State annotation должна быть конкретной зарегистрированной моделью.
  Union нескольких конкретных моделей разрешен.
- `SUBCLASSES`: State annotation может быть общим Pydantic generic-родителем,
  например `OrderState[OrderStatus]`. При финализации он разворачивается во все
  зарегистрированные конкретные подклассы:

```python
@audit.reaction(match=MatchMode.SUBCLASSES)
async def audit_order(state: OrderState[OrderStatus]) -> Retry | None:
    ...
```

Generic-родитель должен быть специализирован тем же state enum, что и схема.

### `finalize`

```python
processor.finalize() -> FSMProcessor
```

Разрешает аннотации, компилирует определения и проверяет локальные конфликты.
Метод идемпотентен и возвращает тот же процессор.

После финализации:

- новые функции регистрировать нельзя;
- доступны read-only свойства `reactions`, `commands` и `initializers`;
- обращение к этим свойствам до финализации выбрасывает
  `ProcessorDefinitionError`.

`compose_processors()` автоматически финализирует переданные процессоры.

## Композиция и introspection

### `compose_processors`

```python
def compose_processors(*processors: FSMProcessor) -> ComposedGraph: ...
```

Объединяет процессоры одной схемы и валидирует межпроцессорные инварианты:

- передан хотя бы один процессор;
- все процессоры ссылаются на тот же объект `FSMSchema`;
- имена процессоров уникальны;
- для конкретного состояния нет двух переходящих Reactions;
- одна Command Model не принадлежит двум процессорам.

```python
graph = compose_processors(PAYMENTS, DELIVERY)
```

Observer Reactions без переходов не создают `GraphEdge`.

### `ProducerKind`

```python
class ProducerKind(StrEnum):
    REACTION = "reaction"
    COMMAND = "command"
    INITIALIZER = "initializer"
```

Указывает источник ребра графа.

### `ReactionDefinition`

Read-only dataclass с полями:

```python
name: str
function: Callable[..., Any]
match: MatchMode
sources: frozenset[type[BaseState[Any]]]
targets: frozenset[type[BaseState[Any]]]
can_delete: bool
can_retry: bool
can_leave_unchanged: bool
```

Свойство `transitioning` равно `True`, если Reaction может вернуть следующее
состояние или `Delete`.

### `CommandHandlerDefinition`

Read-only dataclass с полями:

```python
name: str
function: Callable[..., Any]
match: MatchMode
command: CommandDefinition
sources: frozenset[type[BaseState[Any]]]
targets: frozenset[type[BaseState[Any]]]
can_delete: bool
can_retry: bool
```

### `InitializerDefinition`

Read-only dataclass с полями:

```python
name: str
function: Callable[..., Any]
command: CommandDefinition
targets: frozenset[type[BaseState[Any]]]
can_retry: bool
```

### `GraphEdge`

Read-only dataclass одного перехода:

```python
producer_kind: ProducerKind
processor: str
function: str
source: type[BaseState[Any]] | None
target: type[BaseState[Any]] | None
deletes: bool
```

`source is None` обозначает Initializer. `target is None` вместе с
`deletes=True` обозначает удаление.

### `ComposedGraph`

Read-only dataclass:

```python
schema: FSMSchema[Any]
processors: tuple[FSMProcessor, ...]
edges: frozenset[GraphEdge]
```

## CLI графа переходов

CLI импортирует указанные Python-модули или файлы, находит в них объекты
`FSMSchema` и `FSMProcessor`, финализирует процессоры и выводит общий граф.

Автоматическое обнаружение:

```console
etcd-fsm graph my_service.orders my_service.payments
etcd-fsm graph tests/order_domain.py
```

Явный выбор объектов:

```console
etcd-fsm graph \
  my_service.orders:ORDER_SCHEMA \
  my_service.payments:PAYMENTS

etcd-fsm graph \
  tests/order_domain.py:ORDER_SCHEMA \
  tests/order_domain.py:PAYMENTS
```

По умолчанию Mermaid выводится в stdout:

```console
etcd-fsm graph tests/order_domain.py > orders.mmd
```

Graphviz DOT можно записать через `-o`:

```console
etcd-fsm graph tests/order_domain.py --format dot -o orders.dot
```

Допустимые форматы: `mermaid` и `dot`. Каждое ребро подписывается именем
процессора, функции и видом producer-а.

Импорт файла исполняет его Python-код так же, как обычный импорт. Файлы внутри
импортируемого package получают обычное имя модуля; standalone-файлы
загружаются под внутренним уникальным именем.

## Исключения

Иерархия публичных исключений:

```text
FSMError
├── DefinitionError
│   ├── SchemaDefinitionError
│   └── ProcessorDefinitionError
└── InvalidFSMKey
```

### `FSMError`

Базовый класс ошибок библиотеки.

### `DefinitionError`

Базовый класс ошибок декларации, обнаруженных до запуска runtime.

### `SchemaDefinitionError`

Некорректная или неполная `FSMSchema`, несоответствие модели discriminator-у
или ошибка перевалидации State/Command Value.

### `ProcessorDefinitionError`

Некорректная сигнатура функции, конфликт Reaction/Command, обращение к
скомпилированным определениям до `finalize()` или регистрация после него.

### `InvalidFSMKey`

Некорректный State Key. Также наследует `ValueError`.

Ошибки Pydantic при обычном создании пользовательских моделей остаются
стандартными `pydantic.ValidationError`.

## Список публичных имен

`etcd_fsm.__all__` содержит:

```text
__version__
Applied
BaseState
Command
CommandDefinition
CommandHandlerDefinition
ComposedGraph
DefinitionError
Delete
FSMError
FSMKey
FSMProcessor
FSMSchema
FrozenModel
GraphEdge
Ignored
InitializerDefinition
InvalidFSMKey
MatchMode
ProcessorDefinitionError
ProducerKind
ReactionDefinition
Rejected
Retry
SchemaDefinitionError
compose_processors
validate_fsm_key
```

Архитектурные термины и семантика будущего runtime описаны отдельно в
[`CONTEXT.md`](../CONTEXT.md) и [`DESIGN-NOTES.md`](./DESIGN-NOTES.md).
