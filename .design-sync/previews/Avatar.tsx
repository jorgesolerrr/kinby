import { Avatar, AvatarBadge, AvatarFallback, AvatarGroup, AvatarGroupCount } from "@kinby/web"

const instances = [
  ["Kinby", "blue"],
  ["Research", "violet"],
  ["Garden", "green"],
  ["Accounts", "amber"],
  ["Reviewer", "red"],
  ["Ops", "gray"],
] as const

export const Colors = () => (
  <div className="flex items-center gap-3">
    {instances.map(([name, color]) => (
      <Avatar key={name}>
        <AvatarFallback variant={color}>{name.charAt(0)}</AvatarFallback>
      </Avatar>
    ))}
    <Avatar>
      <AvatarFallback>JS</AvatarFallback>
    </Avatar>
  </div>
)

export const Shapes = () => (
  <div className="flex items-center gap-3">
    <Avatar shape="circle" size="lg">
      <AvatarFallback variant="blue">K</AvatarFallback>
    </Avatar>
    <Avatar shape="squircle" size="lg">
      <AvatarFallback variant="violet">R</AvatarFallback>
    </Avatar>
    <Avatar shape="square" size="lg">
      <AvatarFallback variant="green">G</AvatarFallback>
    </Avatar>
  </div>
)

export const Sizes = () => (
  <div className="flex items-center gap-3">
    <Avatar size="sm" shape="squircle">
      <AvatarFallback variant="amber">A</AvatarFallback>
    </Avatar>
    <Avatar shape="squircle">
      <AvatarFallback variant="amber">A</AvatarFallback>
    </Avatar>
    <Avatar size="lg" shape="squircle">
      <AvatarFallback variant="amber">A</AvatarFallback>
    </Avatar>
  </div>
)

export const WithBadge = () => (
  <div className="flex items-center gap-4">
    <Avatar size="sm">
      <AvatarFallback variant="blue">K</AvatarFallback>
      <AvatarBadge className="bg-success" />
    </Avatar>
    <Avatar>
      <AvatarFallback variant="violet">R</AvatarFallback>
      <AvatarBadge className="bg-success" />
    </Avatar>
    <Avatar size="lg">
      <AvatarFallback variant="red">O</AvatarFallback>
      <AvatarBadge className="bg-ember" />
    </Avatar>
  </div>
)

export const Group = () => (
  <AvatarGroup>
    {instances.slice(0, 4).map(([name, color]) => (
      <Avatar key={name}>
        <AvatarFallback variant={color}>{name.charAt(0)}</AvatarFallback>
      </Avatar>
    ))}
    <AvatarGroupCount>+2</AvatarGroupCount>
  </AvatarGroup>
)

export const WithName = () => (
  <div className="flex flex-col gap-3 text-sm">
    {instances.slice(0, 3).map(([name, color]) => (
      <div key={name} className="flex items-center gap-2">
        <Avatar size="sm" shape="squircle">
          <AvatarFallback variant={color}>{name.charAt(0)}</AvatarFallback>
        </Avatar>
        <span className="font-medium">{name}</span>
      </div>
    ))}
  </div>
)
