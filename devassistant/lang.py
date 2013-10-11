import re
import sys

from devassistant import command
from devassistant import exceptions

if sys.version_info[0] > 2:
    basestring = str

def parse_for(control_line):
    """Returns name of loop control variable(s) and expression to iterate on.

    For example:
    - given "for $i in $foo", returns (['i'], '$foo')
    - given "for ${i} in $(ls $foo)", returns (['i'], 'ls $foo')
    - given "for $k, $v in $foo", returns (['k', 'v'], '$foo')
    """
    error = 'For loop call must be in form \'for $var in expression\', got: ' + control_line
    regex = re.compile(r'for\s+(\${?\S}?)(?:\s*,\s+(\${?\S}?))?\s+in\s+(\S.+)')
    res = regex.match(control_line).groups()
    if not res:
        raise exceptions.YamlSyntaxError(error)

    control_vars = []
    control_vars.append(get_var_name(res[0]))
    if res[1]:
        control_vars.append(get_var_name(res[1]))
    expr = res[2]

    return (control_vars, expr)

def get_for_control_var_and_eval_expr(comm_type, kwargs):
    """Returns tuple that consists of control variable name and iterable that is result
    of evaluated expression of given for loop.

    For example:
    - given 'for $i in $(echo "foo bar")' it returns (['i'], ['foo', 'bar'])
    - given 'for $i, $j in $foo' it returns (['i', 'j'], [('foo', 'bar')])
    """
    try:
        control_vars, expression = parse_for(comm_type)
    except exceptions.YamlSyntaxError as e:
        logger.error(e)
        raise e
    try:
        eval_expression = evaluate(expression, kwargs)[1]
    except exceptions.YamlSyntaxError as e:
        logger.log(e)
        raise e

    iterval = []
    if len(control_vars) == 2:
        if not isinstance(eval_expression, dict):
            raise exceptions.YamlSyntaxError('Can\'t expand {t} to two control variables.'.\
                    format(t=type(eval_expression)))
        else:
            iterval = list(eval_expression.items())
    elif isinstance(eval_expression, basestring):
        iterval = eval_expression.split()
    return control_vars, iterval

def get_section_from_condition(if_section, else_section, kwargs):
    """Returns section that should be used from given if/else sections by evaluating given
    condition.

    Args:
        if_section - section with if clause
        else_section - section that *may* be else clause (just next section after if_section,
                       this method will check if it really is else); possibly None if not present

    Returns:
        tuple (<0 or 1>, <True or False>, section), where
        - the first member says whether we're going to "if" section (0) or else section (1)
        - the second member says whether we should skip next section during further evaluation
          (True or False - either we have else to skip, or we don't)
        - the third member is the appropriate section to run or None if there is only "if"
          clause and condition evaluates to False
    """
    # check if else section is really else
    skip = True if else_section is not None and else_section[0] == 'else' else False
    if evaluate(if_section[0][2:].strip(), kwargs)[0]:
        return (0, skip, if_section[1])
    else:
        return (1, skip, else_section[1]) if skip else (1, skip, None)

def assign_variable(variable, comm, kwargs):
    """Assigns *result* of expression to variable. If there are two variables separated by
    comma, the first gets assigned *logical result* and the second the *result*.
    The variable is then put into kwargs (overwriting original value, if already there).
    Note, that unlike other methods, this method has to accept kwargs, not **kwargs.

    Even if comm has *logical result* == False, output is still stored and
    this method doesn't fail.

    Args:
        variable: variable (or two variables separated by ",") to assign to
        comm: either another variable or command to run
    """
    comma_count = variable.count(',')
    if comma_count > 1:
        raise exceptions.YamlSyntaxError('Max two variables allowed on left side.')

    res1, res2 = evaluate(comm, kwargs)
    if comma_count == 1:
        var1, var2 = map(lambda v: get_var_name(v), variable.split(','))
        kwargs[var1] = res1
    else:
        var2 = get_var_name(variable)
    kwargs[var2] = res2

def get_var_name(dolar_variable):
    name = dolar_variable.strip()
    name = name.strip('"\'')
    if not name.startswith('$'):
        raise exceptions.YamlSyntaxError('Not a proper variable name: ' + dolar_variable)
    name = name[1:] # strip the dollar
    return name.strip('{}')

def evaluate(expression, kwargs):
    """Evaluates given expression.

    Syntax and semantics:

    - ``$foo``

        - if ``$foo`` is defined:

            - *logical result*: ``True`` **iff** value is not empty and it is not
              ``False``
            - *result*: value of ``$foo``
          - otherwise:

              - *logical result*: ``False``
              - *result*: empty string
    - ``$(commandline command)``

        - if ``commandline command`` has return value 0:

            - *logical result*: ``True``

        - otherwise:

            - *logical result*: ``False``

        - regardless of *logical result*, *result* always contains both stdout
          and stderr lines in the order they were printed by ``commandline command``
    - ``not`` - negates the *logical result* of an expression, while leaving
      *result* intact, can only be used once (no, you can't use ``not not not $foo``, sorry)
    - ``defined $foo`` - works exactly as ``$foo``, but has *logical result*
      ``True`` even if the value is empty or ``False``

    Returns:
        tuple (logical result, result) - see above for explanation

    Raises:
        exceptions.YamlSyntaxError if expression is malformed
    """
    # was command successful?
    success = True
    # command output
    output = ''
    invert_success = False
    # if we have an arbitrary structure, just return it
    if not isinstance(expression, str):
        return (True if expression else False, expression)
    expr = expression.strip()
    if expr.startswith('not '):
        invert_success = True
        expr = expr[4:]

    if expr.startswith('$('): # only one expression: "$(expression)"
        try:
            output = command.Command('cl_n', expr[2:-1], kwargs).run()
        except exceptions.RunException as ex:
            success = False
            output = ex.output
    elif expr.startswith('$') or expr.startswith('"$'):
        var_name = get_var_name(expr)
        if var_name in kwargs and kwargs[var_name]:
            success = True
            output = kwargs[var_name]
        else:
            success = False
    elif expr.startswith('defined '):
        varname = get_var_name(expr[8:])
        success = varname in kwargs
        output = kwargs.get(varname, '')
    else:
        raise exceptions.YamlSyntaxError('Not a valid expression: ' + expression)

    return (success if not invert_success else not success, output)