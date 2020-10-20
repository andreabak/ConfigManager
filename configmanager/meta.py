"""Base classes and meta facilities for Config and ConfigSection classes"""

from __future__ import annotations

import configparser
import os
from dataclasses import dataclass, fields as dataclass_fields, asdict as dataclass_asdict, InitVar
from os import PathLike
from typing import (Union, get_args, get_origin, ClassVar, MutableMapping, Any, Type, Callable,
                    Optional, Mapping, Sequence)


__all__ = [
    "ConfigBase",
    "ConfigSectionBase",
    "ConfigSectionAutoNamed",
    "CheckNoneNonOptionalFieldsMixin",
    "AUTO_NAME",
    "PathType",
    "is_optional_type",
]


PathType = Union[bytes, str, PathLike]


def is_optional_type(tp):
    """
    Check if a type annotation is Optional, or allows None
    :param tp: The type annotation
    :return: True if the type is optional else False
    """
    args = get_args(tp)
    origin = get_origin(tp)
    return origin is Union and args is not None and type(None) in args


class _AUTO_NAME:
    """
    A Sentinel object to signal that the class attribute ``section_name``
    of a ``ConfigSectionBase`` subclass needs to be autmatically set.
    Using a class for better repr.
    """
    pass


AUTO_NAME = _AUTO_NAME()


@dataclass
class CheckNoneNonOptionalFieldsMixin:
    """
    Mixin for dataclasses to ensure that at instantiation time all fields
    that do not have an optional type have a valid, not None, value.
    This allows us not to worry about optional dataclass fields order and related dataclasses restrictions,
    so that optional fields checking is delayed to instantiation time using type annotations checking,
    and even required fields can initially be defined as None.
    """
    def __post_init__(self):
        """
        Check that all non optional fields have a valid not None value
        :raise TypeError: On a required field that is None
        """
        for field in dataclass_fields(self):
            if getattr(self, field.name, None) is None and not is_optional_type(field.type):
                raise TypeError(f'{field.name} was not specified for {self.__class__.__name__}')


@dataclass
class ConfigSectionBase(CheckNoneNonOptionalFieldsMixin):
    """
    Base class for configuration sections.
    Any declared subclass should be decorated with @dataclass to ensure they're correctly defined as dataclasses.
    The rationale is to use a dataclass to define the known section parameters
    and enforce their existence only if defined as dataclass fields.
    It can load values from a configparser SectionProxy and do some basic typecasting from str values
    and vice-versa convert an existing instance to a configparser SectionProxy, converting all fields to str.
    """
    section_name: ClassVar[str] = None
    """Defines the section name as serialized to a configparser.SectionProxy name.
    This can be set to the special placeholder AUTO_NAME and, when used together with a ConfigBase instance,
    section_name will be automatically set from the associated ConfigBase's field name
    (this is set at class level, and there is no immediate collision checking mechanism)"""

    def __init_subclass__(cls, **kwargs):
        """
        Ensure that at subclass definition time "section_name" class attribute is not None
        :param kwargs: keyword arguments passed to class definition
        """
        if getattr(cls, 'section_name', None) is None:
            raise SyntaxError(f'Subclasses of ConfigSection must specify a class variable "section_name"'
                              f' as a string representing the config section name'
                              f' or AUTO_NAME to generate the "section_name" from a ConfigBase class\' field name')

    def __post_init__(self):
        """
        Dataclass initialization method. Here we make sure that at initialization time
        the "section_name" class attribute is a valid name and not AUTO_NAME anymore (assertion only).
        """
        assert self.section_name is not AUTO_NAME, f"{self.__class__.__name__} has uninitialized section_name"
        super().__post_init__()

    @classmethod
    def convert_field_types_from_strings(cls, source_data: Mapping[str, str]) -> MutableMapping:
        """
        Convert a mapping of str: str to a mapping of class fields name: values, doing some basic typecasting
        :param source_data: The source data
        :return: The converted mapping representing fields data
        :raise KeyError: If any of the keys of source_data are not known class' fields names
        """
        cls_field_nameset = {field.name for field in dataclass_fields(cls)}
        unknown_source_nameset = set(source_data.keys()) - cls_field_nameset
        if unknown_source_nameset:
            raise KeyError(f'Unknown config field names in source data: {", ".join(unknown_source_nameset)}')
        converted_fields: MutableMapping[str, Any] = {}
        for field in dataclass_fields(cls):
            if field.name in source_data:
                value: Any
                # TODO: Add more special cases
                if field.type == bool:
                    value = source_data[field.name] in ('1', 1, 'true', 'True', 'TRUE')
                else:
                    value = field.type(source_data[field.name])
                converted_fields[field.name] = value
        return converted_fields

    @classmethod
    def from_config_section(cls, section: configparser.SectionProxy) -> ConfigSectionBase:
        """
        Convert a configparser config section and return an initialized class instance.
        :param section: The configparser.SectionProxy instance to be converted
        :return: The initialized class instance
        :raise ValueError: If the name of the "section" object does not match the class' "section_name" attribute
        """
        if cls.section_name != section.name:
            raise ValueError(f'ConfigSection {cls.__name__} name "{cls.section_name}"'
                             f' does not match with configparser SectionProxy name "{section.name}"')
        converted_fields = cls.convert_field_types_from_strings(section)
        # noinspection PyArgumentList
        return cls(**converted_fields)

    def to_config_section(self, parser: configparser.RawConfigParser) -> configparser.SectionProxy:
        """
        Convert a ConfigSectionBase instance to a config section object, doing some basic typecasting to str.
        :param parser: A configparser.RawConfigParser instance to initialize the SectionProxy
        :return: The instantiated SectionProxy
        """
        section = configparser.SectionProxy(parser=parser, name=self.section_name)
        data = dataclass_asdict(self)
        for name, value in data.items():  # TODO: Maybe skip values that == default for that field
            section[name] = str(value)
        return section


@dataclass
class ConfigSectionAutoNamed(ConfigSectionBase):
    """
    A ConfigSectionBase subclass with AUTO_NAME as default "section_name" value.
    """
    section_name: ClassVar[str] = AUTO_NAME


def config_dataclass(cls: Type[ConfigBase]) -> Type[ConfigBase]:
    """
    A decorator for ConfigBase subclasses to ensure they're correctly defined.
    It converts the class to a dataclass, then checks that all fields are of ConfigSectionBase type,
    then ensures that each section name matches the field name,
    and sets all sections with AUTO_NAME "section_name" to the respective field name.
    :param cls: The input class definition
    :return: The converted and checked class
    :raise TypeError: If any of the fields' type is not a subclass of ConfigSectionBase
    :raise ValueError: If any of the section names do not match the respective field name
    """
    cls: Type[ConfigBase] = dataclass(cls)
    fields_to_check = cls.get_section_fields()
    wrong_type_fields = {field.name for field in fields_to_check
                         if not issubclass(field.type, ConfigSectionBase)}
    if wrong_type_fields:
        raise TypeError(f'Some fields of ConfigBase subclass {cls.__name__}'
                        f' are not of type ConfigSectionBase: {", ".join(wrong_type_fields)}')
    mismatched_field_names = {field.name for field in fields_to_check
                              if field.type.section_name is not AUTO_NAME
                              and field.type.section_name != field.name}
    if mismatched_field_names:
        raise ValueError(f'Some field names of ConfigBase subclass {cls.__name__}'
                         f' do not match respective ConfigSectionBase section_name(s):'
                         f' {", ".join(mismatched_field_names)}')
    for field in fields_to_check:
        if field.type.section_name is AUTO_NAME:
            field.type.section_name = field.name
    return cls


@dataclass
class ConfigBase(CheckNoneNonOptionalFieldsMixin):
    """
    Base class for configuration objects.
    Any declared subclass should be decorated with @config_dataclass to ensure they're correctly defined.
    The rationale is to have a dataclass only with fields of ConfigSectionBase type
    representing the configuration sections. The field name must match the section name,
    or AUTO_NAME must be used for "section_name" of the ConfigSectionBase class.
    It provides functionality to load and save a configuration from an .ini file using configparser
    and apply the loaded values to the sections ensuring all the names match in the defined dataclasses.
    """
    _config_parser_factory: ClassVar[Callable[..., configparser.RawConfigParser]] = configparser.ConfigParser
    """The callable "factory" that returns an instance of configparser.RawConfigParser when called,
    that is then used to parse the .ini file(s)"""

    config_parser: InitVar[configparser.RawConfigParser] = None
    """An init-only argument that passes the RawConfigParser instance used to initialize the dataclass"""

    config_path: InitVar[Optional[PathType]] = None
    """An init-only argument that specifies the path of the .ini file used for initialization"""

    @classmethod
    def get_section_fields(cls):
        """
        Returns the dataclass fields that represent config sections
        """
        return tuple(field for field in dataclass_fields(cls))

    def __post_init__(self, config_parser: configparser.RawConfigParser, config_path: Optional[PathType] = None):
        """
        Dataclass init method.
        :param config_parser: The configparser.RawConfigParser used to initialize this instance
        :param config_path: An optional path of the .ini file used by configparser to load the values from
        """
        super().__post_init__()
        self._config_parser: configparser.RawConfigParser = config_parser
        self._config_path: Optional[PathType] = None

    @classmethod
    def load(cls, path: Optional[Union[PathType, Sequence[PathType]]] = None, **parser_kwargs) -> ConfigBase:
        """
        Create a ConfigBase instance, optionally loading values from a .ini file
        :param path: An optional path or sequence of paths of .ini files to load the config values from
        :param parser_kwargs: Optional keyword arguments to be passed to configparser factory
        :return: An initialized instance of ConfigBase
        :raise ValueError: If the provided path type is not known
        """
        parser = cls._config_parser_factory(**parser_kwargs)
        config_path = None
        if path is not None:
            # noinspection PyTypeChecker
            if isinstance(path, (tuple, list)):
                paths = path
            elif isinstance(path, (str, bytes, PathType)):
                paths = (path,)
                config_path = path
            else:
                raise ValueError(f'Unknown path type "{type(path).__name__}"')
            for p in paths:
                if not os.path.exists(p):
                    raise FileNotFoundError(f'Config file path "{p}" does not exist.')
                parser.read(p)
        cls_sections: MutableMapping[str, ConfigSectionBase] = {}
        for field in cls.get_section_fields():
            assert issubclass(field.type, ConfigSectionBase)
            if field.name in parser:
                section = field.type.from_config_section(parser[field.name])
            else:
                section = field.type()  # Will raise an exception if Section class has non-optional fields
            cls_sections[field.name] = section
        # noinspection PyArgumentList
        return cls(**cls_sections, config_parser=parser, config_path=config_path)

    def save(self, path: Optional[PathType] = None, encoding='utf8', **write_kwargs):
        """
        Save an .ini file using the configparser used to initialize the instance
        :param path: The .ini file path to save the config to.
                     If omitted, it will try to be derived from the .ini file path used to initialize the instance.
        :param encoding: The encoding for the output file. Defaults to utf8
        :param write_kwargs: Optional keyword arguments for configparser write function
        :raise ValueError: If no path was provided nor any path could be extracted from initialization arguments
        """
        if path is None:
            path = self._config_path
        if path is None:
            raise ValueError(f'Argument "path" was not specified, and {self.__class__.__name__}'
                             f' instance does not have any config_path stored')
        for field in self.get_section_fields():
            section = getattr(self, field.name)
            assert isinstance(section, ConfigSectionBase)
            self._config_parser[field.name] = section.to_config_section(self._config_parser)
        with open(path, 'w', encoding=encoding) as fp:
            self._config_parser.write(fp, **write_kwargs)
