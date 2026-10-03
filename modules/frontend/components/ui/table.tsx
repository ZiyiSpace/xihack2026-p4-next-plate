"use client"

import * as React from "react"

import { cn } from "@/lib/utils"

const TableLabels = React.createContext<string[]>([])

// Opt-in mobile records keep the original table and its column associations.
function collectLabels(children: React.ReactNode): string[] {
  const labels: string[] = []
  React.Children.forEach(children, child => {
    if (!React.isValidElement<{ children?: React.ReactNode }>(child)) return
    if (child.type === TableHead) {
      labels.push(typeof child.props.children === "string" ? child.props.children : "")
    } else if (child.type === TableHeader || child.type === TableRow || child.type === React.Fragment) {
      labels.push(...collectLabels(child.props.children))
    }
  })
  return labels
}

function Table({ className, children, mobileLayout = "scroll", ...props }: React.ComponentProps<"table"> & { mobileLayout?: "scroll" | "cards" }) {
  return <TableLabels.Provider value={collectLabels(children)}>
    <div data-slot="table-container" data-mobile-layout={mobileLayout} className="relative w-full overflow-x-auto">
      <table data-slot="table" className={cn("w-full caption-bottom text-sm", className)} {...props}>{children}</table>
    </div>
  </TableLabels.Provider>
}

function TableHeader({ className, ...props }: React.ComponentProps<"thead">) {
  return (
    <thead
      data-slot="table-header"
      className={cn("[&_tr]:border-b", className)}
      {...props}
    />
  )
}

function TableBody({ className, ...props }: React.ComponentProps<"tbody">) {
  return (
    <tbody
      data-slot="table-body"
      className={cn("[&_tr:last-child]:border-0", className)}
      {...props}
    />
  )
}

function TableFooter({ className, ...props }: React.ComponentProps<"tfoot">) {
  return (
    <tfoot
      data-slot="table-footer"
      className={cn(
        "border-t bg-muted/50 font-medium [&>tr]:last:border-b-0",
        className
      )}
      {...props}
    />
  )
}

function TableRow({ className, children, ...props }: React.ComponentProps<"tr">) {
  const labels = React.useContext(TableLabels)
  const cells = React.Children.map(children, (child, index) =>
    React.isValidElement<React.ComponentProps<"td">>(child) && child.type === TableCell
      ? React.cloneElement(child, { "data-label": labels[index] } as React.ComponentProps<"td">)
      : child
  )
  return (
    <tr
      data-slot="table-row"
      className={cn(
        "border-b transition-colors hover:bg-muted/50 has-aria-expanded:bg-muted/50 data-[state=selected]:bg-muted",
        className
      )}
      {...props}
    >{cells}</tr>
  )
}

function TableHead({ className, ...props }: React.ComponentProps<"th">) {
  return (
    <th
      data-slot="table-head"
      className={cn(
        "h-10 px-2 text-left align-middle font-medium whitespace-nowrap text-foreground [&:has([role=checkbox])]:pr-0 [&>[role=checkbox]]:translate-y-[2px]",
        className
      )}
      {...props}
    />
  )
}

function TableCell({ className, ...props }: React.ComponentProps<"td">) {
  return (
    <td
      data-slot="table-cell"
      className={cn(
        "p-2 align-middle whitespace-nowrap [&:has([role=checkbox])]:pr-0 [&>[role=checkbox]]:translate-y-[2px]",
        className
      )}
      {...props}
    />
  )
}

function TableCaption({
  className,
  ...props
}: React.ComponentProps<"caption">) {
  return (
    <caption
      data-slot="table-caption"
      className={cn("mt-4 text-sm text-muted-foreground", className)}
      {...props}
    />
  )
}

export {
  Table,
  TableHeader,
  TableBody,
  TableFooter,
  TableHead,
  TableRow,
  TableCell,
  TableCaption,
}
